"""Runtime settings, read once from the environment.

Everything local: the app talks only to llama-swap (text + vision) and the
local embedding server. No cloud or paid endpoints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = _path("COOKT_DATA_DIR", ROOT / "data")
    frontend_dist: Path = _path("COOKT_FRONTEND_DIST", ROOT / "frontend" / "dist")
    host: str = os.getenv("COOKT_HOST", "0.0.0.0")
    port: int = int(os.getenv("COOKT_PORT", "8088"))

    # Local models. llama-swap runs one slot shared with other clients: every call is
    # serialized through a cross-process file lock (see cookt.llm).
    llama_base_url: str = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    text_model: str = os.getenv("COOKT_TEXT_MODEL", "qwen3.6-35b-a3b")
    vision_model: str = os.getenv("COOKT_VISION_MODEL", "qwen3.8-27b")
    # Copy-edit proposals (cookt.copyedit): judgment-heavy, so the larger general model.
    copyedit_model: str = os.getenv("COOKT_COPYEDIT_MODEL", "qwen3.8-flash-next")
    llama_timeout_seconds: float = float(os.getenv("LLAMA_TIMEOUT_SECONDS", "600"))
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "http://127.0.0.1:8111/v1").rstrip(
        "/"
    )
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "qwen3-embedding-0.6b")
    whisper_base_url: str = os.getenv("WHISPER_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")

    # URL fetching (ported from recipe-table url_safety).
    fetch_user_agent: str = os.getenv(
        "FETCH_USER_AGENT",
        "Mozilla/5.0 (compatible; cookt/1.0; household recipe importer)",
    )
    fetch_timeout_seconds: float = float(os.getenv("FETCH_TIMEOUT_SECONDS", "20"))
    fetch_max_bytes: int = int(os.getenv("FETCH_MAX_BYTES", str(4 * 1024 * 1024)))
    fetch_max_redirects: int = int(os.getenv("FETCH_MAX_REDIRECTS", "5"))
    image_max_bytes: int = int(os.getenv("IMAGE_MAX_BYTES", str(12 * 1024 * 1024)))
    bulk_import_max_bytes: int = int(os.getenv("BULK_IMPORT_MAX_BYTES", str(25 * 1024 * 1024)))
    bulk_import_max_recipes: int = int(os.getenv("BULK_IMPORT_MAX_RECIPES", "500"))
    # Cookbook EPUBs carry full-size photos, so they get their own (larger) upload limit.
    epub_max_bytes: int = int(os.getenv("EPUB_MAX_BYTES", str(600 * 1024 * 1024)))
    epub_max_recipes: int = int(os.getenv("EPUB_MAX_RECIPES", "1500"))
    bulk_import_max_uncompressed_bytes: int = int(
        os.getenv("BULK_IMPORT_MAX_UNCOMPRESSED_BYTES", str(50 * 1024 * 1024))
    )
    # robots.txt is only consulted when a caller opts in (bulk/automated fetches);
    # user-initiated single imports do not block on it.
    robots_cache_seconds: int = int(os.getenv("ROBOTS_CACHE_SECONDS", "3600"))
    image_max_per_recipe: int = int(os.getenv("IMAGE_MAX_PER_RECIPE", "9"))
    # Photo import: longest side sent to the vision model, and pages per import.
    vision_max_side: int = int(os.getenv("VISION_MAX_SIDE", "1600"))
    vision_max_pages: int = int(os.getenv("VISION_MAX_PAGES", "8"))
    # Social video audio: faster-whisper model name and the largest audio download.
    whisper_model: str = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-small")
    audio_max_bytes: int = int(os.getenv("AUDIO_MAX_BYTES", str(40 * 1024 * 1024)))
    # Optional shared PIN, only needed if the app is ever exposed beyond the tailnet.
    pin: str = os.getenv("COOKT_PIN", "")
    # Web Push (VAPID) contact: an https: URL or mailto: address for the app's operator.
    # Apple's push service rejects placeholder hosts, so set this for iPad timer alerts.
    vapid_subject: str = os.getenv("COOKT_VAPID_SUBJECT", "mailto:cookt@example.com")
    run_worker: bool = _bool("COOKT_RUN_WORKER", True)
    test_mode: bool = _bool("COOKT_TEST_MODE", False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cookt.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()
