"""«Цифровой сомелье»: рекомендации по карточке вина из каталога.

Всё строится на полях, которые уже есть в каталоге сайта (категория, сорта,
регион, крепость, температура подачи, блюда) — без внешних данных и моделей,
поэтому ответ мгновенный и объяснимый: у каждой рекомендации есть причина.

Модуль намеренно не зависит от FastAPI и Qdrant: на вход — список payload
каталога, на выход — словари. Так его можно проверить на catalog.jsonl
без поднятого сервиса.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SITE_MEDIA_PATH = Path(__file__).resolve().parent / "site_media.json"


@dataclass(frozen=True)
class SiteMedia:
    """Картинки справочников сайта (tools/site-media/fetch_site_media.py).

    Хранятся путями `/uploads/...`; готовый URL нужного размера собирает
    `image_url` — сайт отдаёт картинки через ресайз-API, как и фото бутылок.
    """

    dishes: dict[str, str] = field(default_factory=dict)
    regions: dict[str, str] = field(default_factory=dict)
    grapes: dict[str, dict[str, str | None]] = field(default_factory=dict)
    image_api: str = "https://api.vino-svoe.ru/v1/img/str-api"

    @classmethod
    def load(cls, path: Path = SITE_MEDIA_PATH, image_api: str | None = None) -> "SiteMedia":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        kwargs = {"image_api": image_api} if image_api else {}
        return cls(data.get("dishes", {}), data.get("regions", {}), data.get("grapes", {}), **kwargs)

    def image_url(self, path: str | None, size: int) -> str | None:
        if not path:
            return None
        filename = path.lstrip("/").removeprefix("uploads/")
        return f"{self.image_api.rstrip('/')}/{size}/{size}/resize/uploads/{filename}"

    def dish(self, name: str) -> str | None:
        # размеры — вдвое больше, чем на сайте: 72 px блюда, 44 px миниатюры, для retina
        return self.image_url(self.dishes.get(name), 144)

    def region(self, name: str | None) -> str | None:
        return self.image_url(self.regions.get(name or ""), 88)

    def grape(self, name: str | None) -> str | None:
        return self.image_url((self.grapes.get(name or "") or {}).get("image"), 88)

    def grape_background(self, grapes: Iterable[str]) -> str | None:
        """Фото виноградника для блока под характеристиками — по первому сорту, у которого оно есть."""
        for name in grapes:
            background = (self.grapes.get(name) or {}).get("background")
            if background:
                return self.image_url(background, 800)
        return None

# ── Блюда ────────────────────────────────────────────────────────────────────
# Названия блюд на сайте — свободный справочник: «Легкие закуски» и
# «Лёгкие закуски», «Морепродукты» и «Рыба и морепродукты». Для сравнения вин
# между собой они сводятся в семейства, для подсказок — в группы с советом.

# название на сайте → (группа совета, семейство для сходства, значок)
_DISHES: dict[str, tuple[str, str, str]] = {
    "Сыры": ("cheese", "cheese", "🧀"),
    "Рыба и морепродукты": ("fish", "fish", "🐟"),
    "Блюда из рыбы": ("fish", "fish", "🐟"),
    "Морепродукты": ("seafood", "fish", "🦐"),
    "Устрицы": ("oysters", "fish", "🦪"),
    "Мясо и стейки": ("meat", "meat", "🥩"),
    "BBQ": ("bbq", "meat", "🍖"),
    "Мясное ассорти": ("charcuterie", "meat", "🥓"),
    "Паштеты": ("pate", "meat", "🍞"),
    "Блюда из птицы": ("poultry", "poultry", "🍗"),
    "Легкие закуски": ("snacks", "snacks", "🫒"),
    "Лёгкие закуски": ("snacks", "snacks", "🫒"),
    "Закуски": ("snacks", "snacks", "🫒"),
    "Брускетты": ("snacks", "snacks", "🥖"),
    "Салаты": ("salads", "veg", "🥗"),
    "Свежие овощи": ("salads", "veg", "🥒"),
    "Овощи гриль": ("veg", "veg", "🫑"),
    "Запеченные овощи": ("veg", "veg", "🫑"),
    "Паста": ("pasta", "pasta", "🍝"),
    "Пицца": ("pizza", "pasta", "🍕"),
    "Несладкая выпечка": ("pastry", "pasta", "🥐"),
    "Выпечка и десерты": ("desserts", "dessert", "🍰"),
    "Десерты": ("desserts", "dessert", "🍰"),
    "Фруктово-ягодные десерты": ("desserts", "dessert", "🍓"),
    "Мороженое": ("desserts", "dessert", "🍨"),
    "Шоколад": ("chocolate", "dessert", "🍫"),
    "Фрукты": ("fruits", "dessert", "🍇"),
    "Азиатская кухня": ("asian", "world", "🥢"),
    "Острое": ("asian", "world", "🌶️"),
    "Кавказская кухня": ("caucasian", "world", "🍢"),
    "Средиземноморская кухня": ("mediterranean", "world", "🫒"),
    "Кухни народов мира": ("world", "world", "🌍"),
    "Русская кухня": ("russian", "world", "🥟"),
    "Фастфуд": ("fastfood", "world", "🍔"),
}

# Совет по группе блюд в зависимости от стиля вина. Стиль: red, white, rose,
# orange, sparkling, sweet; «*» — общий совет, если для стиля нет своего.
_PAIRING_TIPS: dict[str, dict[str, str]] = {
    "cheese": {
        "red": "Твёрдые выдержанные: пармезан, гауда, чеддер, качотта",
        "white": "Козий сыр, фета, моцарелла, бри и камамбер",
        "orange": "Выдержанные и пикантные: пекорино, комте, сыры с плесенью",
        "rose": "Молодые и сливочные: рикотта, халуми, сулугуни",
        "sparkling": "Бри, камамбер и пармезан кусочками",
        "sweet": "Голубые сыры — горгонзола, рокфор — контраст сладости и соли",
        "*": "Сырная тарелка из 3–4 видов разной выдержки",
    },
    "fish": {
        "red": "Жирная рыба на гриле: тунец, лосось — без сливочных соусов",
        "white": "Белая рыба на пару или гриле, лосось в сливочном соусе",
        "orange": "Жирная и копчёная рыба, рыба с пряностями",
        "rose": "Лосось, тунец, рыба на гриле с овощами",
        "sparkling": "Слабосолёная красная рыба, тартар, икра",
        "*": "Рыба на гриле с лимоном и травами",
    },
    "seafood": {
        "white": "Креветки, мидии в белом вине, кальмары",
        "orange": "Креветки в чесночном масле, морепродукты с пряностями",
        "rose": "Креветки, осьминог на гриле, паэлья",
        "sparkling": "Креветки темпура, морской коктейль",
        "*": "Морепродукты на гриле с лимоном",
    },
    "oysters": {
        "sparkling": "Классика: устрицы и сухое игристое — кислотность освежает",
        "*": "Свежие устрицы с лимоном",
    },
    "meat": {
        "red": "Стейк, томлёная говядина, баранина — танины смягчают жир",
        "rose": "Телятина, свинина на гриле",
        "orange": "Свинина и телятина с пряностями",
        "*": "Мясо на гриле средней прожарки",
    },
    "bbq": {
        "red": "Рёбрышки, шашлык, бургеры с дымком",
        "rose": "Шашлык из свинины и курицы, колбаски гриль",
        "*": "Мясо и овощи на углях",
    },
    "charcuterie": {
        "red": "Хамон, салями, копчёная колбаса",
        "sparkling": "Прошутто и брезаола — игристое освежает после жирного",
        "*": "Мясная тарелка: вяленое мясо и колбасы",
    },
    "pate": {
        "sweet": "Паштет из печени и фуа-гра — сладость подчёркивает текстуру",
        "*": "Паштет из печени на тосте",
    },
    "poultry": {
        "red": "Утка, индейка, курица в томатном соусе",
        "white": "Курица в сливочном соусе, запечённая индейка",
        "*": "Курица и индейка, запечённые с травами",
    },
    "snacks": {
        "sparkling": "Канапе, оливки, чипсы — игристое любит солёное",
        "*": "Брускетты, оливки, хумус, овощные чипсы",
    },
    "salads": {"*": "Салаты с лёгкой заправкой, свежие овощи и зелень"},
    "veg": {"*": "Овощи гриль, баклажаны, перец, цукини"},
    "pasta": {
        "red": "Паста с томатным или мясным соусом: болоньезе, аматричана",
        "white": "Паста со сливочным соусом или морепродуктами",
        "*": "Паста с томатным соусом",
    },
    "pizza": {"*": "Пицца маргарита, пепперони"},
    "pastry": {"*": "Несладкая выпечка: киш, хачапури, пироги"},
    "desserts": {
        "sweet": "Фруктовые тарты, панна-котта — десерт не слаще вина",
        "sparkling": "Лёгкие десерты: безе, ягодные тарталетки",
        "*": "Фруктовые и ягодные десерты",
    },
    "chocolate": {
        "red": "Горький шоколад 70%+",
        "*": "Шоколад и шоколадные десерты",
    },
    "fruits": {"*": "Сезонные фрукты и ягоды"},
    "asian": {
        "white": "Азиатская кухня с умеренной остротой: том ям, пад тай",
        "sweet": "Острое: сладость вина гасит жгучесть",
        "*": "Роллы, вок, блюда с имбирём",
    },
    "caucasian": {
        "red": "Шашлык, чахохбили, хинкали",
        "*": "Хачапури, пхали, сыр сулугуни",
    },
    "mediterranean": {"*": "Средиземноморская кухня: оливки, овощи, морепродукты, травы"},
    "world": {"*": "Блюда разных кухонь с пряностями"},
    "russian": {"*": "Пельмени, пироги, соленья"},
    "fastfood": {"*": "Бургеры, пицца, картофель фри"},
}

# ── Стиль вина ───────────────────────────────────────────────────────────────

_SPARKLING = {"брют", "экстра брют"}
_SWEET = {"сладкое", "полусладкое"}
_COLOR_STYLE = {"красное": "red", "белое": "white", "розовое": "rose", "оранжевое": "orange"}


@dataclass(frozen=True)
class WineStyle:
    color: str | None  # «Красное», как в категории сайта
    sweetness: str | None  # «сухое», «брют»…
    style: str  # ключ для советов: red, white, rose, orange, sparkling, sweet

    @property
    def sparkling(self) -> bool:
        return self.sweetness in _SPARKLING


def wine_style(category: str | None) -> WineStyle:
    """«Белое экстра брют» → цвет «Белое», сладость «экстра брют», стиль sparkling."""
    parts = (category or "").strip().split(maxsplit=1)
    color = parts[0] if parts else None
    sweetness = parts[1].lower() if len(parts) > 1 else None
    if sweetness in _SPARKLING:
        style = "sparkling"
    elif sweetness in _SWEET:
        style = "sweet"
    else:
        style = _COLOR_STYLE.get((color or "").lower(), "white")
    return WineStyle(color, sweetness, style)


def parse_temperature(raw: str | None) -> tuple[int, int] | None:
    """«10-12», «10–12», «8 - 12 °C» → (10, 12); одно число → (n, n)."""
    if not raw:
        return None
    numbers = [int(n) for n in re.findall(r"\d+", raw)]
    if not numbers:
        return None
    low, high = min(numbers[:2]), max(numbers[:2])
    return low, high


def _body(alcohol: float | None, style: WineStyle) -> tuple[str, int] | None:
    """Тело вина по крепости — грубо, но это единственный сигнал в каталоге."""
    if alcohol is None or style.sparkling:
        return None
    if alcohol < 11.5:
        return "лёгкое", 1
    if alcohol < 13.5:
        return "среднее", 2
    return "полнотелое", 3


def _serving_tip(temperature: tuple[int, int] | None) -> str | None:
    if temperature is None:
        return None
    high = temperature[1]
    if high <= 8:
        return "Хорошо охладите: 2–3 часа в холодильнике или 20 минут в ведёрке со льдом"
    if high <= 12:
        return "Достаньте из холодильника за 15–20 минут до подачи"
    if high <= 15:
        return "Слегка охладите: 20–30 минут в холодильнике"
    return "Температура прохладной комнаты; в жару — 10–15 минут в холодильнике"


def _glass(style: WineStyle, grapes: list[str], alcohol: float | None) -> str:
    grapes_lower = " ".join(grapes).lower()
    if style.sparkling:
        return "Бокал-тюльпан или флюте — пузырьки сохранятся дольше"
    if style.style == "sweet":
        return "Небольшой десертный бокал"
    if style.style == "red":
        if "пино" in grapes_lower:
            return "Бургундский бокал с широкой чашей — раскроет аромат Пино Нуара"
        if alcohol is not None and alcohol >= 13.5:
            return "Бордоский бокал — высокий, с большой чашей"
        return "Универсальный бокал для красного вина"
    if style.style == "orange":
        return "Бокал для белого вина с широкой чашей"
    if style.style == "rose":
        return "Бокал для белого или розового вина"
    return "Бокал для белого вина"


def _aeration(style: WineStyle, alcohol: float | None) -> str | None:
    if style.style == "red" and style.sweetness == "сухое" and alcohol is not None and alcohol >= 13.5:
        return "Дайте вину подышать 20–30 минут в декантере или бокале"
    if style.style == "orange":
        return "Можно дать вину подышать 10–15 минут — аромат станет глубже"
    return None


def _alcohol_note(alcohol: float | None) -> str | None:
    if alcohol is None:
        return None
    if alcohol < 11:
        return "Лёгкое по крепости — подойдёт для долгого ужина и аперитива"
    if alcohol < 13.5:
        return "Умеренная крепость — универсально к ужину"
    return "Высокая крепость — лучше с сытными блюдами"


def _dish_info(name: str) -> tuple[str, str, str]:
    return _DISHES.get(name, ("world", "world", "🍽️"))


def dish_icon(name: str) -> str:
    return _dish_info(name)[2]


def _pairing_tip(group: str, style: str) -> str | None:
    tips = _PAIRING_TIPS.get(group, {})
    return tips.get(style) or tips.get("*")


def _families(dishes: Iterable[str]) -> set[str]:
    return {_dish_info(d)[1] for d in dishes}


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


# ── Сомелье ──────────────────────────────────────────────────────────────────


class Sommelier:
    """Карточка вина с советами + похожие вина + вина к блюду.

    Каталог держится в памяти целиком (≈2100 записей, единицы мегабайт) и
    берётся из payload Qdrant на старте: индекс остаётся
    единственным источником истины о том, какие вина вообще есть.
    """

    def __init__(self, payloads: Iterable[dict[str, Any]], media: SiteMedia | None = None) -> None:
        self.media = media or SiteMedia()
        self._wines: dict[str, dict[str, Any]] = {}
        for payload in payloads:
            slug = payload.get("slug")
            if slug:
                self._wines[slug] = payload

    def __len__(self) -> int:
        return len(self._wines)

    def __contains__(self, slug: str) -> bool:
        return slug in self._wines

    @property
    def has_pairing_data(self) -> bool:
        """False — индекс построен до того, как в payload появились блюда."""
        return any(w.get("dishes") for w in self._wines.values())

    def get(self, slug: str) -> dict[str, Any] | None:
        return self._wines.get(slug)

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        """Текстовый поиск по каталогу: название, производитель, сорт, регион, категория.

        Все слова запроса должны найтись (как префиксы слов карточки); выше —
        совпадения в названии, затем по народному рейтингу.
        """
        words = [w for w in re.split(r"[\s,.;:«»\"'()\-]+", _norm(query)) if w]
        if not words:
            return []
        scored = []
        for wine in self._wines.values():
            title = _norm(wine.get("title"))
            fields = " ".join(
                [title, _norm(wine.get("manufacturer")), _norm(wine.get("region")), _norm(wine.get("category"))]
                + [_norm(g) for g in wine.get("grapes") or []]
            )
            tokens = re.split(r"[\s,.;:«»\"'()\-]+", fields)
            if not all(any(t.startswith(w) for t in tokens) for w in words):
                continue
            title_tokens = re.split(r"[\s,.;:«»\"'()\-]+", title)
            in_title = sum(any(t.startswith(w) for t in title_tokens) for w in words)
            scored.append((in_title, title.startswith(words[0]), wine.get("public_rating") or 0, wine))
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        return [w for *_, w in scored[:limit]]

    def dish_names(self) -> list[tuple[str, int]]:
        """Все блюда каталога с числом вин — для экрана «подобрать к блюду»."""
        counts: dict[str, int] = {}
        for wine in self._wines.values():
            for dish in wine.get("dishes") or []:
                counts[dish] = counts.get(dish, 0) + 1
        return sorted(counts.items(), key=lambda x: -x[1])

    def card_media(self, wine: dict[str, Any]) -> dict[str, str | None]:
        """Картинки карточки как на сайте: миниатюры региона и сорта, фон с виноградом."""
        grapes = wine.get("grapes") or []
        return {
            "region_image": self.media.region(wine.get("region")),
            "grape_image": next((u for u in map(self.media.grape, grapes) if u), None),
            "background_image": self.media.grape_background(grapes),
        }

    def advise(self, wine: dict[str, Any]) -> dict[str, Any]:
        """Советы сомелье по одной карточке."""
        style = wine_style(wine.get("category"))
        alcohol = wine.get("alcohol")
        temperature = parse_temperature(wine.get("temperature"))
        body = _body(alcohol, style)
        grapes = wine.get("grapes") or []

        pairings = []
        for dish in wine.get("dishes") or []:
            group, _, icon = _dish_info(dish)
            pairings.append({
                "dish": dish,
                "icon": icon,
                "image": self.media.dish(dish),
                "tip": _pairing_tip(group, style.style),
            })

        return {
            "summary": self._summary(wine, style, body, temperature),
            "style": {
                "key": style.style,
                "color": style.color,
                "sweetness": style.sweetness,
                "sparkling": style.sparkling,
                "body": body[0] if body else None,
                "body_level": body[1] if body else None,
            },
            "serving": {
                "temperature": _format_temperature(temperature),
                "temperature_min": temperature[0] if temperature else None,
                "temperature_max": temperature[1] if temperature else None,
                "tip": _serving_tip(temperature),
                "glass": _glass(style, grapes, alcohol),
                "aeration": _aeration(style, alcohol),
            },
            "alcohol": {"value": alcohol, "note": _alcohol_note(alcohol)},
            "pairings": pairings,
        }

    def similar(self, wine: dict[str, Any], limit: int = 6, per_manufacturer: int = 2) -> list[dict[str, Any]]:
        """Похожие вина по стилю, сорту, региону, блюдам и крепости.

        Не больше `per_manufacturer` вин одного производителя: иначе у крупных
        виноделен выдача превращается в их линейку целиком.
        """
        slug = wine.get("slug")
        style = wine_style(wine.get("category"))
        grapes = {_norm(g) for g in wine.get("grapes") or []}
        region = _norm(wine.get("region"))
        families = _families(wine.get("dishes") or [])
        alcohol = wine.get("alcohol")

        scored = []
        for other in self._wines.values():
            if other.get("slug") == slug:
                continue
            reasons: list[str] = []
            score = 0.0
            other_style = wine_style(other.get("category"))

            if other.get("category") and other.get("category") == wine.get("category"):
                score += 3.0
                reasons.append(f"тоже {other['category'].lower()}")
            elif other_style.color and other_style.color == style.color:
                score += 1.5
            elif other_style.style == style.style:
                score += 1.0

            other_grapes = {_norm(g) for g in other.get("grapes") or []}
            shared = grapes & other_grapes
            if shared:
                score += 2.0 * len(shared) / len(grapes | other_grapes) + 1.0
                names = [g for g in other.get("grapes") or [] if _norm(g) in shared]
                reasons.append("тот же сорт: " + ", ".join(names[:2]))

            if region and _norm(other.get("region")) == region:
                score += 1.0
                reasons.append(f"тоже {other.get('region')}")

            other_families = _families(other.get("dishes") or [])
            if families and other_families:
                overlap = len(families & other_families) / len(families | other_families)
                score += 1.5 * overlap
                if overlap >= 0.5:
                    reasons.append("к тем же блюдам")

            other_alcohol = other.get("alcohol")
            if alcohol is not None and other_alcohol is not None:
                score += 0.5 * (1 - min(abs(alcohol - other_alcohol) / 3, 1))

            rating = other.get("public_rating")
            if rating:
                score += 0.3 * rating / 5

            scored.append((score, reasons, other))

        scored.sort(key=lambda x: -x[0])
        result, by_manufacturer = [], {}
        for score, reasons, other in scored:
            maker = other.get("manufacturer_slug") or other.get("manufacturer")
            if by_manufacturer.get(maker, 0) >= per_manufacturer:
                continue
            by_manufacturer[maker] = by_manufacturer.get(maker, 0) + 1
            result.append({**other, "similarity": round(score, 3), "reasons": reasons[:3]})
            if len(result) >= limit:
                break
        return result

    def for_dish(
        self, dish: str, *, style: str | None = None, exclude: str | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Вина к блюду: сначала точное совпадение блюда, затем то же семейство.

        Внутри — по народному рейтингу: при прочих равных показываем вина,
        которые понравились людям.
        """
        target_group, target_family, _ = _dish_info(dish)
        scored = []
        for wine in self._wines.values():
            if wine.get("slug") == exclude:
                continue
            if style and wine_style(wine.get("category")).style != style:
                continue
            dishes = wine.get("dishes") or []
            if dish in dishes:
                match = 2
            elif any(_dish_info(d)[0] == target_group for d in dishes):
                match = 1.5
            elif target_family in _families(dishes):
                match = 1
            else:
                continue
            scored.append((match, wine.get("public_rating") or 0, wine))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [w for _, _, w in scored[:limit]]

    @staticmethod
    def _summary(
        wine: dict[str, Any],
        style: WineStyle,
        body: tuple[str, int] | None,
        temperature: tuple[int, int] | None,
    ) -> str:
        """Одна фраза, которую можно прочитать у полки: что это и как пить."""
        category = (wine.get("category") or "Вино").strip()
        parts = [category[0].upper() + category[1:].lower()]
        if body:
            parts[0] += f", {body[0]} тело"
        alcohol = wine.get("alcohol")
        if alcohol:
            parts[0] += f" ({alcohol:g}%)"
        if temperature:
            parts.append(f"подавать при {_format_temperature(temperature)}")
        dishes = wine.get("dishes") or []
        if dishes:
            parts.append("к блюдам: " + ", ".join(d.lower() for d in dishes[:3]))
        return "; ".join(parts) + "."


def _format_temperature(temperature: tuple[int, int] | None) -> str | None:
    if temperature is None:
        return None
    low, high = temperature
    return f"{low}–{high} °C" if low != high else f"{low} °C"
