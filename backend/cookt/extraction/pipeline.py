"""High-level import API: the contract the app calls.

Order of preference everywhere: deterministic structure first (Schema.org JSON-LD, then
microdata, Paprika exports, the conservative plain-text parser), and the local model only
when that fails. The model path is always available (no opt-in flag) and is guarded by
the verbatim-evidence and >= 88% token-coverage checks in ``model.py``/``vision.py``.

Every public function raises ``ExtractionError`` with a person-readable message (and a
stable ``code``) on failure.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup, Tag

from ..config import settings
from ..document import RecipeDocumentV2
from ..images import ImageError, detect_image_media_type, prepare_image_for_model
from ..llm import ModelError, ModelUnavailable
from ..url_safety import (
    FetchError,
    UrlSafetyError,
    fetch_recipe_page,
    resolve_public_addresses,
)
from .model import (
    CompletionClient,
    LocalModelClient,
    conventional_to_document,
    extract_conventional_detailed,
)
from .parser import (
    ConventionalRecipe,
    ImportedRecipe,
    ImportIssue,
    ParseResult,
    RecipeExport,
    SourceRecipeMetadata,
    clipboard_html_to_text,
    clipboard_text_overlaps,
    duration_minutes,
    parse_html_recipe_export,
    parse_paprika_archive,
    parse_plain_text_recipe,
    parse_structured_recipe,
    parse_zip_recipe_export,
    sanitized_main_text,
)
from .vision import VisionClient, extract_photo_recipe

log = logging.getLogger("cookt.extraction")

__all__ = [
    "Extraction",
    "ExtractionError",
    "canonicalize_url",
    "extract_from_images",
    "extract_from_paprika",
    "extract_from_paprika_detailed",
    "extract_from_text",
    "extract_from_url",
    "fetch_social_caption",
    "transcribe_audio_url",
]

MAX_TEXT_CHARS = 40_000


class ExtractionError(RuntimeError):
    def __init__(self, message: str, code: str = "extraction_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Extraction:
    document: RecipeDocumentV2  # lossless display text
    method: str  # 'jsonld' | 'microdata' | 'paprika' | 'text' | 'llm' | 'vision'
    source_url: str | None = None
    canonical_url: str | None = None
    source_site: str | None = None
    source_author: str | None = None
    image_urls: list[str] = field(default_factory=list)
    nutrition: dict | None = None  # publisher block verbatim (JSON-LD NutritionInformation)
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    yield_text: str | None = None
    hints: dict = field(default_factory=dict)  # cuisine/category/keywords (+ extras) from source
    coverage: float | None = None  # token coverage when an LLM was involved
    uncertain_lines: list[str] = field(default_factory=list)  # photo: spans to confirm
    transcript: str | None = None  # OCR transcript (photos)
    warnings: list[str] = field(default_factory=list)
    # Embedded image bytes from exports (Paprika photo_data / ZIP images): (bytes, media type).
    image_data: list[tuple[bytes, str]] = field(default_factory=list, repr=False)


# --- URL identity -------------------------------------------------------------------------

TRACKING_PARAMETERS = {
    "_ga",
    "_gl",
    "_hsenc",
    "_hsmi",
    "cmpid",
    "dclid",
    "fbclid",
    "gclid",
    "gclsrc",
    "igsh",
    "igshid",
    "mbid",
    "msclkid",
    "ncid",
    "oly_anon_id",
    "oly_enc_id",
    "rb_clickid",
    "ref",
    "ref_",
    "ref_src",
    "referrer",
    "s_cid",
    "sc_cid",
    "smid",
    "smtyp",
    "source",
    "srsltid",
    "ttclid",
    "twclid",
    "vero_id",
    "wickedid",
    "yclid",
}
TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_", "__hs")


def canonicalize_url(url: str) -> str:
    """Stable identity for a recipe URL (dedupe key), not a fetchable address.

    https, lowercase host without ``www.``, no fragment, no default port, tracking
    parameters removed, remaining parameters sorted, no trailing slash.
    """

    raw = (url or "").strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ExtractionError("That link is not a valid URL", "invalid_url") from exc
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise ExtractionError("Only http(s) links can be imported", "invalid_url")
    host = parts.hostname.rstrip(".").casefold()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ExtractionError("That link has an invalid host name", "invalid_url") from exc
    host = host.removeprefix("www.")
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "")
    path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_PARAMETERS
            and not key.casefold().startswith(TRACKING_PREFIXES)
        )
    )
    return urlunsplit(("https", netloc, path, query, ""))


def _site_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url if "://" in url else f"https://{url}").hostname
    except ValueError:
        return None
    return (host or "").casefold().removeprefix("www.") or None


def _safe_canonical(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return canonicalize_url(url)
    except ExtractionError:
        return None


# --- shared result building ---------------------------------------------------------------

_NOTE_TIME = re.compile(
    r"^(?P<label>prep|cook|total|ready in)(?:\s+time)?\s*:\s*(?P<value>.+)$", re.IGNORECASE
)


def _times_from_notes(notes: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for note in notes:
        match = _NOTE_TIME.match(note.strip())
        if not match:
            continue
        label = match.group("label").casefold()
        key = "total" if label == "ready in" else label
        minutes = duration_minutes(match.group("value"))
        if minutes is not None:
            found.setdefault(key, minutes)
    return found


def _absolute_urls(urls: list[str], base: str | None) -> list[str]:
    output: list[str] = []
    for value in urls:
        absolute = urljoin(base, value) if base else value
        if absolute.startswith(("http://", "https://")):
            output.append(absolute[:4_096])
    return list(dict.fromkeys(output))


def _hints(metadata: SourceRecipeMetadata, site_name: str | None = None) -> dict[str, Any]:
    hints: dict[str, Any] = {
        "cuisine": metadata.cuisines,
        "category": metadata.categories,
        "keywords": metadata.keywords,
        "diets": metadata.diets,
        "description": metadata.description,
        "site_name": site_name or metadata.publisher,
        "rating_value": metadata.rating_value,
        "rating_count": metadata.rating_count,
        "date_published": metadata.date_published,
    }
    return {key: value for key, value in hints.items() if value not in (None, [], "")}


def _build(
    recipe: ConventionalRecipe,
    method: str,
    *,
    metadata: SourceRecipeMetadata | None = None,
    source_url: str | None = None,
    canonical_url: str | None = None,
    source_site: str | None = None,
    site_name: str | None = None,
    image_urls: list[str] | None = None,
    coverage: float | None = None,
    warnings: list[str] | None = None,
) -> Extraction:
    metadata = metadata or SourceRecipeMetadata()
    try:
        document = conventional_to_document(recipe, source_site=source_site)
    except ValueError as exc:
        raise ExtractionError(
            f"The recipe could not be converted: {str(exc).splitlines()[0]}", "invalid_recipe"
        ) from exc
    note_times = _times_from_notes(recipe.prep_notes)
    return Extraction(
        document=document,
        method=method,
        source_url=source_url,
        canonical_url=canonical_url,
        source_site=source_site,
        source_author=recipe.author,
        image_urls=image_urls or [],
        nutrition=metadata.nutrition,
        prep_minutes=duration_minutes(metadata.prep_time) or note_times.get("prep"),
        cook_minutes=duration_minutes(metadata.cook_time) or note_times.get("cook"),
        total_minutes=duration_minutes(metadata.total_time) or note_times.get("total"),
        yield_text=recipe.yield_text,
        hints=_hints(metadata, site_name),
        coverage=coverage,
        warnings=warnings or [],
    )


def _method_for(parser_path: str) -> str:
    return {"json_ld": "jsonld", "microdata": "microdata", "plain_text": "text"}.get(
        parser_path, parser_path
    )


def _model_extract(text: str, client: CompletionClient | None):
    try:
        return extract_conventional_detailed(text[:MAX_TEXT_CHARS], client or LocalModelClient())
    except ModelUnavailable as exc:
        raise ExtractionError(
            "The local recipe model is unavailable right now; try again shortly.",
            "inference_unavailable",
        ) from exc
    except ModelError as exc:
        raise ExtractionError(
            "The recipe could not be extracted reliably (the model dropped or invented "
            f"content). {str(exc)[:300]}",
            "invalid_model_output",
        ) from exc


def _page_metadata(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    def meta(*selectors: dict[str, str]) -> str | None:
        for attrs in selectors:
            node = soup.find("meta", attrs=attrs)
            if isinstance(node, Tag) and node.get("content"):
                return re.sub(r"\s+", " ", str(node["content"])).strip() or None
        return None

    images = [
        value
        for value in (
            meta({"property": "og:image"}, {"property": "og:image:secure_url"}),
            meta({"name": "twitter:image"}, {"name": "twitter:image:src"}),
        )
        if value
    ]
    return {
        "site_name": meta({"property": "og:site_name"}, {"name": "application-name"}),
        "image_urls": _absolute_urls(images, base_url),
    }


# --- URL ----------------------------------------------------------------------------------


def extract_from_url(url: str, *, client: CompletionClient | None = None) -> Extraction:
    try:
        fetched = fetch_recipe_page(url, allow_restricted=True)
    except UrlSafetyError as exc:
        raise ExtractionError(str(exc), "unsafe_url") from exc
    except FetchError as exc:
        raise ExtractionError(str(exc), exc.code) from exc

    page = _page_metadata(fetched.html, fetched.canonical_url)
    warnings: list[str] = []
    parsed = parse_structured_recipe(fetched.html, fetched.canonical_url)
    coverage: float | None = None
    if parsed is not None:
        recipe, metadata, method = parsed.recipe, parsed.source_metadata, parsed.parser_path
        if fetched.restricted:
            warnings.append(
                "The page looked paywalled or login-gated; imported from the publisher's "
                "structured recipe data."
            )
    else:
        if fetched.restricted:
            raise ExtractionError(
                "Paywalled and login-required recipe pages cannot be imported. Paste the "
                "recipe text instead.",
                "restricted_page",
            )
        text = sanitized_main_text(fetched.html, fetched.canonical_url)
        if len(text) < 200:
            raise ExtractionError(
                "No readable recipe content was found; the page may need JavaScript. "
                "Paste the recipe text instead.",
                "javascript_only",
            )
        result = _model_extract(text, client)
        recipe, metadata, method = result.recipe, SourceRecipeMetadata(), "llm"
        coverage = result.coverage
        warnings.extend(result.warnings)
        warnings.append("No structured recipe data on the page; extracted with the local model.")

    image_urls = _absolute_urls([*metadata.image_urls, *page["image_urls"]], fetched.canonical_url)
    return _build(
        recipe,
        _method_for(method),
        metadata=metadata,
        source_url=url.strip(),
        canonical_url=canonicalize_url(fetched.canonical_url),
        source_site=fetched.source_site,
        site_name=page["site_name"],
        image_urls=image_urls,
        coverage=coverage,
        warnings=warnings,
    )


# --- pasted text / clipboard HTML / social captions ---------------------------------------


def extract_from_text(
    text: str,
    html: str | None = None,
    source_url: str | None = None,
    *,
    client: CompletionClient | None = None,
) -> Extraction:
    text = (text or "").strip()
    html = (html or "").strip() or None
    if not text and not html:
        raise ExtractionError("Paste some recipe text first", "empty_input")
    site = _site_of(source_url)
    common = {
        "source_url": source_url,
        "canonical_url": _safe_canonical(source_url),
        "source_site": site,
    }

    parsed: ParseResult | None = None
    if html:
        structured = parse_structured_recipe(html, source_url or "")
        if structured is not None:
            return _build(
                structured.recipe,
                _method_for(structured.parser_path),
                metadata=structured.source_metadata,
                image_urls=_absolute_urls(structured.source_metadata.image_urls, source_url),
                **common,
            )
        semantic = clipboard_html_to_text(html)
        if not text:
            text = semantic
        elif clipboard_text_overlaps(text, semantic):
            parsed = parse_plain_text_recipe(semantic)
    parsed = parsed or parse_plain_text_recipe(text)
    if parsed is not None:
        return _build(parsed.recipe, "text", metadata=parsed.source_metadata, **common)

    result = _model_extract(text, client)
    return _build(
        result.recipe,
        "llm",
        coverage=result.coverage,
        warnings=result.warnings,
        **common,
    )


# --- Paprika ------------------------------------------------------------------------------


def _paprika_export(data: bytes, filename: str) -> RecipeExport:
    if len(data) > settings.bulk_import_max_bytes:
        raise ExtractionError("That export is larger than the import limit", "too_large")
    suffix = PurePosixPath(filename or "").suffix.casefold()
    max_recipes = settings.bulk_import_max_recipes
    try:
        if data[:2] == b"\x1f\x8b" or suffix in {".paprikarecipes", ".paprikarecipe"}:
            return parse_paprika_archive(
                data,
                max_recipes=max_recipes,
                max_uncompressed_bytes=settings.bulk_import_max_uncompressed_bytes,
            )
        if data[:2] == b"PK":
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = [name.casefold() for name in archive.namelist()]
            if any(name.endswith(".paprikarecipe") for name in names):
                return parse_paprika_archive(
                    data,
                    max_recipes=max_recipes,
                    max_uncompressed_bytes=settings.bulk_import_max_uncompressed_bytes,
                )
            return parse_zip_recipe_export(
                data,
                max_recipes=max_recipes,
                max_html_bytes=settings.bulk_import_max_uncompressed_bytes,
            )
        return parse_html_recipe_export(data, max_recipes=max_recipes)
    except ExtractionError:
        raise
    except Exception as exc:  # zipfile/gzip/bs4 raise a variety of errors on bad input
        raise ExtractionError(
            str(exc).splitlines()[0][:300] or "The export could not be read", "invalid_export"
        ) from exc


def _paprika_extraction(item: ImportedRecipe) -> Extraction:
    metadata = item.source_metadata
    source_url = item.source_url if (item.source_url or "").startswith("http") else None
    image_urls = [
        image.source_url
        for image in item.images
        if image.source_url and image.source_url.startswith(("http://", "https://"))
    ]
    extraction = _build(
        item.recipe,
        "paprika",
        metadata=metadata,
        source_url=source_url,
        canonical_url=_safe_canonical(source_url),
        source_site=_site_of(source_url),
        image_urls=_absolute_urls([*metadata.image_urls, *image_urls], source_url),
        warnings=[
            note.removeprefix("Import warning: ")
            for note in item.recipe.prep_notes
            if note.startswith("Import warning:")
        ],
    )
    extraction.image_data = [
        (image.data, image.media_type) for image in item.images if image.data and image.media_type
    ]
    return extraction


def extract_from_paprika_detailed(
    data: bytes, filename: str
) -> tuple[list[Extraction], list[ImportIssue]]:
    export = _paprika_export(data, filename)
    extractions: list[Extraction] = []
    issues = list(export.issues)
    for item in export.recipes:
        try:
            extractions.append(_paprika_extraction(item))
        except ExtractionError as exc:
            issues.append(ImportIssue(index=item.index, title=item.recipe.title, message=str(exc)))
    return extractions, issues


def extract_from_paprika(data: bytes, filename: str) -> list[Extraction]:
    """Paprika HTML export, ZIP of HTML exports, or native ``.paprikarecipes`` archive."""

    extractions, issues = extract_from_paprika_detailed(data, filename)
    if not extractions:
        detail = issues[0].message if issues else "no recipes were found"
        raise ExtractionError(f"Nothing could be imported: {detail}", "invalid_export")
    for issue in issues:
        log.warning("paprika import skipped #%s %r: %s", issue.index, issue.title, issue.message)
    return extractions


# --- photos / handwritten cards -----------------------------------------------------------


def extract_from_images(
    images: list[tuple[bytes, str]],
    handwritten: bool = False,
    *,
    client: VisionClient | None = None,
) -> Extraction:
    """1..N photos of one recipe in page order -> one recipe (OCR transcript + extraction)."""

    if not images:
        raise ExtractionError("Add at least one photo", "empty_input")
    if len(images) > settings.vision_max_pages:
        raise ExtractionError(
            f"Too many photos; the limit is {settings.vision_max_pages} pages per recipe",
            "too_many_pages",
        )
    prepared: list[tuple[bytes, str]] = []
    for index, (data, _media_type) in enumerate(images, 1):
        if len(data) > settings.image_max_bytes:
            raise ExtractionError(f"Photo {index} is larger than the upload limit", "too_large")
        if detect_image_media_type(data) is None:
            raise ExtractionError(
                f"Photo {index} is not a supported image (JPEG, PNG, WebP, GIF or AVIF)",
                "unsupported_image",
            )
        try:
            prepared.append(prepare_image_for_model(data))
        except ImageError as exc:
            raise ExtractionError(f"Photo {index} could not be read", "unsupported_image") from exc

    try:
        result = extract_photo_recipe(
            prepared, client or LocalModelClient(), handwritten=handwritten
        )
    except ModelUnavailable as exc:
        raise ExtractionError(
            "The local vision model is unavailable right now; try again shortly.",
            "inference_unavailable",
        ) from exc
    except ModelError as exc:
        raise ExtractionError(
            f"The photo could not be read reliably. {str(exc)[:300]}", "invalid_model_output"
        ) from exc

    extraction = _build(
        result.recipe,
        "vision",
        coverage=result.coverage,
        warnings=result.warnings,
    )
    extraction.uncertain_lines = result.uncertain_lines
    extraction.transcript = result.transcript
    if result.uncertain_lines:
        extraction.warnings.append(
            f"{len(result.uncertain_lines)} line(s) were hard to read; confirm them."
        )
    return extraction


# --- social video helpers (yt-dlp + local faster-whisper) ---------------------------------

SOCIAL_HOSTS = {
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "x.com",
    "twitter.com",
    "pinterest.com",
    "threads.net",
}


def _validate_social_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise ExtractionError("That link is not a valid URL", "invalid_url") from exc
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme.casefold() not in {"http", "https"} or not host:
        raise ExtractionError("Only http(s) links are supported", "invalid_url")
    if parts.username or parts.password or port not in {None, 80, 443}:
        raise ExtractionError("That link is not supported", "invalid_url")
    if not any(host == item or host.endswith(f".{item}") for item in SOCIAL_HOSTS):
        raise ExtractionError("Only social/video links are supported here", "invalid_url")
    try:
        resolve_public_addresses(host, port or 443)
    except UrlSafetyError as exc:
        raise ExtractionError(str(exc), "unsafe_url") from exc
    return url.strip()


def _yt_dlp() -> str:
    binary = shutil.which("yt-dlp") or str(Path.home() / ".local" / "bin" / "yt-dlp")
    if not Path(binary).exists():
        raise ExtractionError("yt-dlp is not installed", "ytdlp_unavailable")
    return binary


_YT_DLP_BASE = ["--ignore-config", "--no-playlist", "--no-progress", "--ies", "default,-generic"]


def fetch_social_caption(url: str, *, timeout: float = 90) -> dict[str, Any]:
    """Caption/description metadata for a social or video post (no media download).

    Feed ``caption`` (plus ``transcribe_audio_url`` output if needed) to
    ``extract_from_text``.
    """

    target = _validate_social_url(url)
    try:
        completed = subprocess.run(
            [_yt_dlp(), *_YT_DLP_BASE, "--dump-single-json", "--skip-download", target],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError("The post took too long to load", "fetch_timeout") from exc
    if completed.returncode != 0:
        raise ExtractionError("The post's caption could not be loaded", "fetch_failed")
    try:
        info = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExtractionError("The post's caption could not be read", "fetch_failed") from exc
    caption = "\n\n".join(
        part for part in (info.get("title"), info.get("description")) if isinstance(part, str)
    )
    return {
        "caption": caption.strip(),
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or target,
        "duration": info.get("duration"),
    }


def transcribe_audio_url(url: str, *, timeout: float = 900) -> str:
    """Download a social video's audio with yt-dlp and transcribe it on the local
    faster-whisper server (``settings.whisper_base_url``).

    The whisper container is auto-toggled and may be stopped; this never starts it and
    raises ``ExtractionError("whisper unavailable")`` when it does not answer.
    """

    target = _validate_social_url(url)
    try:
        health = httpx.get(settings.whisper_base_url.removesuffix("/v1") + "/health", timeout=5)
        health.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExtractionError("whisper unavailable", "whisper_unavailable") from exc

    max_mb = max(1, settings.audio_max_bytes // (1024 * 1024))
    with tempfile.TemporaryDirectory(prefix="cookt-audio-") as workdir:
        try:
            completed = subprocess.run(
                [
                    _yt_dlp(),
                    *_YT_DLP_BASE,
                    "-f",
                    f"bestaudio[filesize<{max_mb}M]/bestaudio/worst",
                    "--max-filesize",
                    f"{max_mb}M",
                    "-o",
                    str(Path(workdir) / "audio.%(ext)s"),
                    target,
                ],
                capture_output=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError("The video took too long to download", "fetch_timeout") from exc
        files = [path for path in Path(workdir).iterdir() if path.name.startswith("audio.")]
        if completed.returncode != 0 or not files:
            raise ExtractionError("The video's audio could not be downloaded", "fetch_failed")
        audio = files[0]
        if audio.stat().st_size > settings.audio_max_bytes:
            raise ExtractionError("The video's audio is too large", "too_large")
        try:
            with audio.open("rb") as handle:
                response = httpx.post(
                    f"{settings.whisper_base_url}/audio/transcriptions",
                    data={"model": settings.whisper_model, "response_format": "json"},
                    files={"file": (audio.name, handle, "application/octet-stream")},
                    timeout=timeout,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExtractionError("whisper unavailable", "whisper_unavailable") from exc
    text = response.json().get("text", "") if response.content else ""
    return re.sub(r"\s+", " ", text).strip()
