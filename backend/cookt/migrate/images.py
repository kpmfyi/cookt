"""Image derivatives for migrated photos: 4:3 display WebP, 4:3 thumbnail WebP, original bytes."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

DISPLAY_MAX_WIDTH = 1600
THUMB_WIDTH = 480
WEBP_QUALITY = 82
THUMB_QUALITY = 78
ASPECT = (4, 3)


@dataclass
class Derived:
    path: str  # relative to the images root
    thumb_path: str
    original_path: str
    width: int
    height: int


def crop_box_4x3(width: int, height: int) -> tuple[int, int, int, int]:
    """Largest centered 4:3 box inside width x height."""

    target = ASPECT[0] / ASPECT[1]
    if width / height > target:
        new_w = round(height * target)
        left = (width - new_w) // 2
        return left, 0, left + new_w, height
    new_h = round(width / target)
    top = (height - new_h) // 2
    return 0, top, width, top + new_h


def _resized(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image
    height = round(width * ASPECT[1] / ASPECT[0])
    return image.resize((width, height), Image.Resampling.LANCZOS)


def derive(original: Path, root: Path, rel_dir: str, stem: str, ext: str) -> Derived:
    """Write <rel_dir>/<stem>display.webp, <stem>thumb.webp, <stem>original.<ext> under root."""

    target = root / rel_dir
    target.mkdir(parents=True, exist_ok=True)
    original_rel = f"{rel_dir}/{stem}original.{ext}"
    shutil.copyfile(original, root / original_rel)
    with Image.open(original) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        cropped = image.crop(crop_box_4x3(image.width, image.height))
    display = _resized(cropped, DISPLAY_MAX_WIDTH)
    thumb = _resized(cropped, THUMB_WIDTH)
    display_rel = f"{rel_dir}/{stem}display.webp"
    thumb_rel = f"{rel_dir}/{stem}thumb.webp"
    display.save(root / display_rel, "WEBP", quality=WEBP_QUALITY, method=6)
    thumb.save(root / thumb_rel, "WEBP", quality=THUMB_QUALITY, method=6)
    return Derived(display_rel, thumb_rel, original_rel, display.width, display.height)
