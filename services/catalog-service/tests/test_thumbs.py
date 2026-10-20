"""Превью бутылок для страницы: строятся при синхронизации, готовые не пересчитываются."""

from PIL import Image

from catalog_scraper.images import THUMB_HEIGHTS, ensure_thumbs, thumb_path


def test_thumbs_built_once_and_broken_photo_skipped(tmp_path):
    bottle = Image.new("RGBA", (1200, 3600), (0, 0, 0, 0))  # фон вырезан, как у фото каталога
    bottle.paste((120, 20, 40, 255), (400, 0, 800, 3600))
    bottle.save(tmp_path / "tall-wine.png")
    Image.new("RGB", (300, 200), "white").save(tmp_path / "small-wine.png")
    (tmp_path / "broken-wine.png").write_bytes(b"not a png")

    assert ensure_thumbs(tmp_path) == 2

    for height in THUMB_HEIGHTS:
        with Image.open(thumb_path(tmp_path, "tall-wine", height)) as im:
            assert im.format == "WEBP" and im.height == height and im.mode == "RGBA"  # прозрачность сохранена
        with Image.open(thumb_path(tmp_path, "small-wine", height)) as im:
            assert im.size == (300, 200)  # не увеличиваем
    assert not thumb_path(tmp_path, "broken-wine", THUMB_HEIGHTS[0]).exists()
    assert ensure_thumbs(tmp_path) == 0  # всё готово — второй прогон ничего не делает
