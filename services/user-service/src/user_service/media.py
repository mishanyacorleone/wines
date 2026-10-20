"""Загруженные картинки: аватары, превью сканов, картинки достижений.

Каждый файл перекодируется Pillow: наружу не уходит то, что прислал клиент
(EXIF с геопозицией, SVG со скриптом, «картинка» с чужим содержимым).
"""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

KINDS = ("avatars", "scans", "achievements")
_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|svg)$")


class InvalidImage(ValueError):
    pass


def _open(raw: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImage("Файл не похож на картинку") from exc
    return ImageOps.exif_transpose(image)


def save_avatar(raw: bytes, media_dir: Path) -> str:
    """Квадрат 256×256 по центру кадра, JPEG."""
    image = ImageOps.fit(_open(raw).convert("RGB"), (256, 256), Image.Resampling.LANCZOS)
    return _write(image, media_dir / "avatars", "jpg", quality=88)


def save_scan_photo(raw: bytes, media_dir: Path) -> str:
    """Превью для истории: до 480 px по длинной стороне, JPEG."""
    image = _open(raw).convert("RGB")
    image.thumbnail((480, 480), Image.Resampling.LANCZOS)
    return _write(image, media_dir / "scans", "jpg", quality=80)


def save_achievement_image(raw: bytes, media_dir: Path) -> str:
    """Значок достижения: вписан в прозрачный квадрат 256×256, PNG.

    Квадрат — чтобы вытянутая картинка не ломала сетку значков в профиле.
    """
    image = _open(raw).convert("RGBA")
    image.thumbnail((256, 256), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    square.paste(image, ((256 - image.width) // 2, (256 - image.height) // 2), image)
    return _write(square, media_dir / "achievements", "png")


def copy_seed_svg(source: Path, media_dir: Path) -> str:
    """Стартовые значки — наши SVG из репозитория, им перекодирование не нужно."""
    target = media_dir / "achievements"
    target.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.svg"
    (target / name).write_bytes(source.read_bytes())
    return name


def media_path(media_dir: Path, kind: str, name: str) -> Path | None:
    """Путь к файлу или None — имя проверяется, чтобы не выйти из папки."""
    if kind not in KINDS or not _NAME.match(name):
        return None
    path = media_dir / kind / name
    return path if path.is_file() else None


def delete(media_dir: Path, kind: str, name: str | None) -> None:
    if name and (path := media_path(media_dir, kind, name)):
        path.unlink(missing_ok=True)


def _write(image: Image.Image, folder: Path, ext: str, **save_kwargs) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    image.save(folder / name, format="JPEG" if ext == "jpg" else "PNG", **save_kwargs)
    return name
