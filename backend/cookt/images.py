"""Image helpers: media-type sniffing, recipe image discovery, bounded downloads, and
downscaling photos before they are sent to the local vision model.

Ported from recipe-table ``images.py`` (quality-upgrade/reroll helpers left behind).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .config import settings
from .url_safety import FetchError, UrlSafetyError, fetch_public_resource

ImageRole = Literal["primary", "step"]


@dataclass(frozen=True, slots=True)
class ImageCandidate:
    url: str
    role: ImageRole
    step_index: int | None = None
    step_text: str | None = None
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class ImagePayload:
    data: bytes
    media_type: str
    source_url: str | None
    role: ImageRole
    step_index: int | None = None
    step_text: str | None = None
    caption: str | None = None


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read raster dimensions without decoding untrusted image pixels."""

    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return (width, height) if width and height else None
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return (width, height) if width and height else None
    if data.startswith(b"\xff\xd8\xff"):
        offset = 2
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0x01, *range(0xD0, 0xDA)}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(data[offset + 3 : offset + 5], "big")
                width = int.from_bytes(data[offset + 5 : offset + 7], "big")
                return (width, height) if width and height else None
            offset += segment_length
        return None
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height
        if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            b1, b2, b3, b4 = data[21:25]
            width = 1 + (((b2 & 0x3F) << 8) | b1)
            height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | (b2 >> 6))
            return width, height
        if kind == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
            return (width, height) if width and height else None
        return None
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        marker = data.find(b"ispe")
        if marker >= 0 and marker + 16 <= len(data):
            width = int.from_bytes(data[marker + 8 : marker + 12], "big")
            height = int.from_bytes(data[marker + 12 : marker + 16], "big")
            return (width, height) if width and height else None
    return None


def image_quality_key(data: bytes) -> tuple[int, int, int, int]:
    dimensions = image_dimensions(data)
    if dimensions is None:
        return (0, 0, 0, len(data))
    width, height = dimensions
    return (1, width * height, min(width, height), len(data))


def detect_image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"avif", b"avis"}:
        return "image/avif"
    return None


def _type_contains(value: Any, expected: str) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(str(item).rsplit("/", 1)[-1] == expected for item in values if item)


def _walk_json_ld(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _walk_json_ld(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _walk_json_ld(value["@graph"])


def _recipe_json_ld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_json_ld(data):
            if _type_contains(item.get("@type"), "Recipe"):
                return item
    return None


def _image_values(value: Any) -> list[tuple[str, int]]:
    if isinstance(value, str):
        return [(value, 0)]
    if isinstance(value, list):
        return [item for value_item in value for item in _image_values(value_item)]
    if not isinstance(value, dict):
        return []
    url = value.get("contentUrl") or value.get("url") or value.get("thumbnailUrl")
    if isinstance(url, dict | list):
        return _image_values(url)
    if not isinstance(url, str):
        return []
    width = value.get("width") or 0
    height = value.get("height") or 0
    try:
        score = int(width) * int(height)
    except (TypeError, ValueError):
        score = 0
    return [(url, score)]


def _best_image_url(value: Any) -> str | None:
    values = _image_values(value)
    if not values:
        return None
    return max(enumerate(values), key=lambda item: (item[1][1], -item[0]))[1][0]


def _best_srcset_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidates: list[tuple[float, str]] = []
    for index, raw_item in enumerate(value.split(",")):
        parts = raw_item.strip().rsplit(maxsplit=1)
        if not parts:
            continue
        url = parts[0]
        score = 1.0 - index / 10_000
        if len(parts) == 2:
            descriptor = parts[1].casefold()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 1_000
            except ValueError:
                pass
        candidates.append((score, url))
    return max(candidates, default=(0, ""))[1] or None


def _tag_image_url(node: Tag) -> str | None:
    for attribute in ("srcset", "data-srcset"):
        if candidate := _best_srcset_url(node.get(attribute)):
            return candidate
    return (
        _clean_text(
            node.get("content")
            or node.get("data-src")
            or node.get("data-lazy-src")
            or node.get("src")
            or node.get("href")
        )
        or None
    )


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _json_steps(value: Any) -> list[tuple[str | None, str, str | None]]:
    if isinstance(value, list):
        return [item for child in value for item in _json_steps(child)]
    if not isinstance(value, dict):
        return []
    if _type_contains(value.get("@type"), "HowToSection"):
        nested = value.get("itemListElement") or value.get("steps") or []
        return _json_steps(nested)
    image_url = _best_image_url(value.get("image"))
    step_text = _clean_text(value.get("text") or value.get("name"))
    if not step_text:
        return []
    caption = _clean_text(value.get("name")) or None
    if caption == step_text:
        caption = None
    return [(image_url, step_text, caption)]


def _absolute_image_url(source_url: str, raw_url: str) -> str | None:
    value = raw_url.strip()
    if not value or value.casefold().startswith(("data:", "blob:")):
        return None
    absolute = urljoin(source_url, value)
    try:
        parts = urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    return absolute[:4096]


def discover_image_candidates(
    html: str,
    source_url: str,
    *,
    max_step_images: int = 8,
) -> list[ImageCandidate]:
    """Discover one representative image and only explicitly-associated step images."""

    soup = BeautifulSoup(html, "html.parser")
    recipe_data = _recipe_json_ld(soup)
    candidates: list[ImageCandidate] = []
    seen: set[str] = set()

    def add_primary(raw_url: str | None) -> None:
        if not raw_url or len([item for item in candidates if item.role == "primary"]) >= 4:
            return
        absolute = _absolute_image_url(source_url, raw_url)
        if not absolute or absolute in seen:
            return
        candidates.append(ImageCandidate(url=absolute, role="primary"))
        seen.add(absolute)

    add_primary(_best_image_url(recipe_data.get("image")) if recipe_data else None)
    recipe_root = soup.find(
        attrs={"itemtype": re.compile(r"(?:https?://)?schema\.org/Recipe", re.IGNORECASE)}
    )
    if isinstance(recipe_root, Tag):
        image_node = recipe_root.find(attrs={"itemprop": re.compile(r"(?:^|\s)image(?:\s|$)")})
        if isinstance(image_node, Tag):
            add_primary(_tag_image_url(image_node))
    for attrs in (
        {"property": "og:image"},
        {"property": "og:image:secure_url"},
        {"name": "twitter:image"},
        {"name": "twitter:image:src"},
    ):
        node = soup.find("meta", attrs=attrs)
        if isinstance(node, Tag) and node.get("content"):
            add_primary(str(node["content"]))
    image_link = soup.find("link", rel=lambda value: value and "image_src" in value)
    if isinstance(image_link, Tag) and image_link.get("href"):
        add_primary(str(image_link["href"]))

    step_values = (
        _json_steps(recipe_data.get("recipeInstructions") or recipe_data.get("instructions"))
        if recipe_data
        else []
    )
    for step_index, (raw_url, step_text, caption) in enumerate(step_values, 1):
        if len([item for item in candidates if item.role == "step"]) >= max_step_images:
            break
        absolute = _absolute_image_url(source_url, raw_url) if raw_url else None
        if not absolute or absolute in seen:
            continue
        candidates.append(
            ImageCandidate(
                url=absolute,
                role="step",
                step_index=step_index,
                step_text=step_text,
                caption=caption,
            )
        )
        seen.add(absolute)
    return candidates


def download_image_candidates(
    candidates: list[ImageCandidate],
    *,
    max_images: int | None = None,
) -> tuple[list[ImagePayload], list[str]]:
    """Download safe raster candidates; individual failures never fail the recipe import."""

    # Primary metadata often exposes several encodings of the same photo. Downloading a
    # few bounded alternatives lets the store compare actual dimensions while the stored
    # image count remains governed by IMAGE_MAX_PER_RECIPE.
    limit = max_images or settings.image_max_per_recipe + 3
    payloads: list[ImagePayload] = []
    failures: list[str] = []
    content_hashes: set[bytes] = set()
    for candidate in candidates[:limit]:
        try:
            fetched = fetch_public_resource(
                candidate.url,
                max_bytes=settings.image_max_bytes,
                accept="image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.8,*/*;q=0.1",
            )
            media_type = detect_image_media_type(fetched.body)
            if media_type is None:
                raise FetchError("unsupported_image", "Resource was not a supported raster image")
            fingerprint = hashlib.sha256(fetched.body).digest()
            if fingerprint in content_hashes:
                continue
            content_hashes.add(fingerprint)
            payloads.append(
                ImagePayload(
                    data=fetched.body,
                    media_type=media_type,
                    source_url=fetched.url,
                    role=candidate.role,
                    step_index=candidate.step_index,
                    step_text=candidate.step_text,
                    caption=candidate.caption,
                )
            )
        except (FetchError, UrlSafetyError) as exc:
            failures.append(f"{candidate.role}:{getattr(exc, 'code', 'unsafe_url')}")
    return payloads, failures


class ImageError(ValueError):
    pass


def prepare_image_for_model(
    data: bytes,
    *,
    max_side: int | None = None,
    quality: int = 85,
) -> tuple[bytes, str]:
    """Normalize a user photo for the vision model: honour EXIF rotation, flatten to RGB,
    shrink so the longest side is at most ``max_side`` and re-encode as JPEG.

    Small JPEGs that need no rotation or resizing are passed through unchanged.
    """

    from PIL import Image, ImageOps, UnidentifiedImageError

    limit = max_side or settings.vision_max_side
    media_type = detect_image_media_type(data)
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            orientation = opened.getexif().get(0x0112, 1)
            if (
                media_type == "image/jpeg"
                and orientation in (None, 1)
                and max(opened.size) <= limit
                and opened.mode in {"RGB", "L"}
            ):
                return data, "image/jpeg"
            image = ImageOps.exif_transpose(opened)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageError("The photo could not be read as an image") from exc
