"""Нормализация изображения перед энкодером.

Кейсодержатель прямо подсказывает: «поработайте с нормализацией фотографии —
это заметно улучшает F1». Здесь минимальная, но обязательная её часть.

Две картинки приходят из разных миров:
  - каталог: PNG с вырезанным фоном (RGBA), вертикальный кроп бутылки;
  - запрос: фото с телефона 3024x4032, бутылка где-то в кадре среди других.

Приводим оба к общему виду: непрозрачный белый фон + квадрат с паддингом
(не растягиваем — иначе бутылка деформируется и перестаёт совпадать
с каталожной).
"""

from __future__ import annotations

from PIL import Image, ImageOps

# Белый фон, а не чёрный: каталожные PNG вырезаны под светлую подложку сайта,
# и на белом они выглядят так же, как на карточке товара.
BACKGROUND = (255, 255, 255)


def flatten_alpha(image: Image.Image) -> Image.Image:
    """Убирает альфа-канал, подкладывая белый фон."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, BACKGROUND)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def pad_to_square(image: Image.Image) -> Image.Image:
    """Дополняет до квадрата, сохраняя пропорции.

    Бутылка — вытянутый объект (типично 340x1080). Прямой resize в квадрат
    раздул бы её по ширине, а фото пользователя деформировалось бы иначе,
    и формы перестали бы совпадать.
    """
    width, height = image.size
    if width == height:
        return image
    side = max(width, height)
    canvas = Image.new("RGB", (side, side), BACKGROUND)
    canvas.paste(image, ((side - width) // 2, (side - height) // 2))
    return canvas


def normalize_image(image: Image.Image) -> Image.Image:
    """Полный конвейер нормализации: альфа -> фон, паддинг до квадрата.

    Ресайз под вход модели делает её собственный процессор — дублировать
    его здесь не нужно.
    """
    # EXIF-поворот: фото с телефона часто лежит боком, с ориентацией в метаданных
    image = ImageOps.exif_transpose(image)
    return pad_to_square(flatten_alpha(image))
