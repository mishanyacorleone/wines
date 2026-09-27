"""Скачивание фото бутылки и конвертация в PNG.

Сайт отдаёт WebP через ресайз-эндпоинт api.vino-svoe.ru. Мы запрашиваем размер
заведомо больше оригинала: API не апскейлит, поэтому в ответ приходит нативное
разрешение картинки.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import httpx
from PIL import Image

from .config import Settings
from .http_client import fetch

logger = logging.getLogger(__name__)

# Pillow по умолчанию отказывается открывать очень большие изображения
# (защита от decompression bomb). Каталожные фото заведомо меньше, оставляем как есть.
Image.MAX_IMAGE_PIXELS = 50_000_000


class ImageError(Exception):
    """Не удалось получить или сконвертировать картинку."""


def build_image_url(relative_url: str, settings: Settings) -> str:
    """'/uploads/simbioz_2021_975b321bc5.webp' -> полный URL ресайз-эндпоинта."""
    filename = relative_url.lstrip("/").removeprefix("uploads/")
    size = settings.image_request_size
    return (
        f"{settings.image_api_url}/v1/img/str-api/{size}/{size}"
        f"/resize/uploads/{filename}"
    )


def to_png(raw: bytes, dest: Path) -> tuple[int, int]:
    """Сохраняет байты изображения как PNG. Возвращает (width, height)."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # Pillow бросает разнородные исключения
        raise ImageError(f"не открывается как изображение: {exc}") from exc

    # Фото бутылок приходят с альфа-каналом (вырезанный фон) — сохраняем его:
    # прозрачность пригодится, если будем подставлять фон при аугментациях.
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".png.part")
    try:
        image.save(tmp, format="PNG", optimize=True)
        tmp.replace(dest)  # атомарная запись: не оставляем битых файлов
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise ImageError(f"не сохраняется как PNG: {exc}") from exc

    return image.width, image.height


async def download_png(
    client: httpx.AsyncClient, relative_url: str, dest: Path, settings: Settings
) -> tuple[int, int]:
    url = build_image_url(relative_url, settings)
    response = await fetch(client, url, settings)

    # Ресайз-эндпоинт на ошибку отвечает 200 + JSON с errorData
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        raise ImageError(f"API вернул ошибку вместо картинки: {response.text[:200]}")

    return to_png(response.content, dest)
