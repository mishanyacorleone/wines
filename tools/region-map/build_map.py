"""Контуры субъектов России для винной карты веб-сканера.

Источник — Natural Earth, admin-1 (1:10m, общественное достояние):
    curl -L -o admin1.geojson \\
      https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson
    python3 tools/region-map/build_map.py admin1.geojson

Пишет в search_api/web/:
  russia-map.json         — все субъекты, упрощённые: общий вид страны (грузится сразу);
  russia-map-detail.json  — те же субъекты подробно: подгружается при приближении.

Проекция — равновеликая коническая Альберса, как на картах России (центральный
меридиан 100° в. д., параллели 52° и 64°). Какие субъекты входят в винный регион
(«Кубань» — Краснодарский край), здесь не задаётся: это данные, их ведёт админ
(сервис пользователей, /v1/map/zones). Скрипт без зависимостей.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "services/search-api/src/search_api/web"

# Официальные названия (ст. 65 Конституции). В Natural Earth часть названий
# сокращена («Дагестан»), у Алтайского края — название республики, а коды Москвы
# и области перепутаны (RU-MOW там — область): названия здесь — по контурам
NAMES = {
    "RU-AD": "Республика Адыгея", "RU-AL": "Республика Алтай", "RU-ALT": "Алтайский край",
    "RU-AMU": "Амурская область", "RU-ARK": "Архангельская область", "RU-AST": "Астраханская область",
    "RU-BA": "Республика Башкортостан", "RU-BEL": "Белгородская область", "RU-BRY": "Брянская область",
    "RU-BU": "Республика Бурятия", "RU-CE": "Чеченская Республика", "RU-CHE": "Челябинская область",
    "RU-CHU": "Чукотский автономный округ", "RU-CU": "Чувашская Республика", "RU-DA": "Республика Дагестан",
    "RU-IN": "Республика Ингушетия", "RU-IRK": "Иркутская область", "RU-IVA": "Ивановская область",
    "RU-KAM": "Камчатский край", "RU-KB": "Кабардино-Балкарская Республика",
    "RU-KC": "Карачаево-Черкесская Республика", "RU-KDA": "Краснодарский край",
    "RU-KEM": "Кемеровская область", "RU-KGD": "Калининградская область", "RU-KGN": "Курганская область",
    "RU-KHA": "Хабаровский край", "RU-KHM": "Ханты-Мансийский автономный округ — Югра",
    "RU-KIR": "Кировская область", "RU-KK": "Республика Хакасия", "RU-KL": "Республика Калмыкия",
    "RU-KLU": "Калужская область", "RU-KO": "Республика Коми", "RU-KOS": "Костромская область",
    "RU-KR": "Республика Карелия", "RU-KRS": "Курская область", "RU-KYA": "Красноярский край",
    "RU-LEN": "Ленинградская область", "RU-LIP": "Липецкая область", "RU-MAG": "Магаданская область",
    "RU-ME": "Республика Марий Эл", "RU-MO": "Республика Мордовия", "RU-MOS": "Москва",
    "RU-MOW": "Московская область", "RU-MUR": "Мурманская область", "RU-NEN": "Ненецкий автономный округ",
    "RU-NGR": "Новгородская область", "RU-NIZ": "Нижегородская область", "RU-NVS": "Новосибирская область",
    "RU-OMS": "Омская область", "RU-ORE": "Оренбургская область", "RU-ORL": "Орловская область",
    "RU-PER": "Пермский край", "RU-PNZ": "Пензенская область", "RU-PRI": "Приморский край",
    "RU-PSK": "Псковская область", "RU-ROS": "Ростовская область", "RU-RYA": "Рязанская область",
    "RU-SA": "Республика Саха (Якутия)", "RU-SAK": "Сахалинская область", "RU-SAM": "Самарская область",
    "RU-SAR": "Саратовская область", "RU-SE": "Республика Северная Осетия — Алания",
    "RU-SMO": "Смоленская область", "RU-SPE": "Санкт-Петербург", "RU-STA": "Ставропольский край",
    "RU-SVE": "Свердловская область", "RU-TA": "Республика Татарстан", "RU-TAM": "Тамбовская область",
    "RU-TOM": "Томская область", "RU-TUL": "Тульская область", "RU-TVE": "Тверская область",
    "RU-TY": "Республика Тыва", "RU-TYU": "Тюменская область", "RU-UD": "Удмуртская Республика",
    "RU-ULY": "Ульяновская область", "RU-VGG": "Волгоградская область", "RU-VLA": "Владимирская область",
    "RU-VLG": "Вологодская область", "RU-VOR": "Воронежская область",
    "RU-YAN": "Ямало-Ненецкий автономный округ", "RU-YAR": "Ярославская область",
    "RU-YEV": "Еврейская автономная область", "RU-ZAB": "Забайкальский край",
    "UA-43": "Республика Крым", "UA-40": "Севастополь",
}
# Короткие подписи на карте — для длинных названий
SHORT = {
    "RU-KHM": "ХМАО — Югра", "RU-YAN": "ЯНАО", "RU-NEN": "НАО", "RU-CHU": "Чукотка",
    "RU-SA": "Якутия", "RU-SE": "Сев. Осетия", "RU-KB": "Кабардино-Балкария", "RU-KC": "Карачаево-Черкесия",
    "RU-YEV": "ЕАО",
}

WIDTH = 1000
COARSE_TOLERANCE = 0.6     # единицы карты шириной 1000: общий вид
DETAIL_TOLERANCE = 0.12    # подробно — для приближения до ×8


class Albers:
    """Равновеликая коническая: φ1, φ2 — стандартные параллели, λ0 — центральный меридиан."""

    def __init__(self, lon0: float, phi1: float, phi2: float, phi0: float) -> None:
        self.lam0 = math.radians(lon0)
        p1, p2 = math.radians(phi1), math.radians(phi2)
        self.n = (math.sin(p1) + math.sin(p2)) / 2
        self.c = math.cos(p1) ** 2 + 2 * self.n * math.sin(p1)
        self.rho0 = math.sqrt(self.c - 2 * self.n * math.sin(math.radians(phi0))) / self.n

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        if lon < 0:  # Чукотка за 180-м меридианом
            lon += 360
        rho = math.sqrt(self.c - 2 * self.n * math.sin(math.radians(lat))) / self.n
        theta = self.n * (math.radians(lon) - self.lam0)
        return rho * math.sin(theta), -(self.rho0 - rho * math.cos(theta))  # y вниз, как в SVG


PROJECT = Albers(100, 52, 64, 56)


def simplify(points, tolerance):
    """Дуглас — Пекер, без рекурсии."""
    if len(points) < 4:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        (x1, y1), (x2, y2) = points[start], points[end]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        best, index = 0.0, -1
        for i in range(start + 1, end):
            x0, y0 = points[i]
            dist = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if dist > best:
                best, index = dist, i
        if best > tolerance and index > 0:
            keep[index] = True
            stack += [(start, index), (index, end)]
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring, tolerance):
    """Замкнутое кольцо режем в самой дальней от начала точке: иначе Дуглас — Пекер
    схлопнул бы его в отрезок (начало и конец совпадают)."""
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 4:
        return ring
    x0, y0 = ring[0]
    far = max(range(len(ring)), key=lambda i: (ring[i][0] - x0) ** 2 + (ring[i][1] - y0) ** 2)
    return simplify(ring[: far + 1], tolerance) + simplify(ring[far:] + ring[:1], tolerance)[1:-1]


def ring_area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def to_path(rings, tolerance, min_area):
    parts = []
    for ring in rings:
        if ring_area(ring) < min_area:
            continue
        pts = simplify_ring(ring, tolerance)
        if len(pts) >= 3:
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
    return "".join(parts)


def inside(x, y, ring):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def edge_distance(x, y, ring):
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1e-12)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def label_point(rings):
    """Точка подписи внутри самого большого кольца, подальше от границ.

    Центр рамки или центр тяжести у вытянутых и вогнутых субъектов (Красноярский
    край, Хабаровский) падает на соседа — ищем по сетке точку, самую удалённую от края.
    """
    ring = simplify_ring(max(rings, key=ring_area), 0.3)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    best, point = -1.0, (sum(xs) / len(xs), sum(ys) / len(ys))
    steps = 24
    for i in range(1, steps):
        for j in range(1, steps):
            x = min(xs) + (max(xs) - min(xs)) * i / steps
            y = min(ys) + (max(ys) - min(ys)) * j / steps
            if inside(x, y, ring):
                d = edge_distance(x, y, ring)
                if d > best:
                    best, point = d, (x, y)
    return [round(point[0], 1), round(point[1], 1)]


def main(source: Path) -> None:
    features = [f for f in json.loads(source.read_text(encoding="utf-8"))["features"]
                if f["properties"].get("iso_3166_2") in NAMES]
    missing = NAMES.keys() - {f["properties"]["iso_3166_2"] for f in features}
    if missing:
        raise SystemExit(f"Нет субъектов в источнике: {sorted(missing)}")

    raw = {}
    for f in features:
        geometry = f["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        raw[f["properties"]["iso_3166_2"]] = [[PROJECT(lon, lat) for lon, lat in ring] for polygon in polygons for ring in polygon]

    xs = [x for rings in raw.values() for ring in rings for x, _ in ring]
    ys = [y for rings in raw.values() for ring in rings for _, y in ring]
    pad = 8
    scale = (WIDTH - 2 * pad) / (max(xs) - min(xs))
    height = (max(ys) - min(ys)) * scale + 2 * pad
    projected = {
        code: [[((x - min(xs)) * scale + pad, (y - min(ys)) * scale + pad) for x, y in ring] for ring in rings]
        for code, rings in raw.items()
    }

    subjects, detail = [], {}
    for code, rings in sorted(projected.items(), key=lambda kv: NAMES[kv[0]]):
        subjects.append({
            "id": code,
            "name": NAMES[code],
            "short": SHORT.get(code, NAMES[code]),
            "d": to_path(rings, COARSE_TOLERANCE, 4),
            "label": label_point(rings),
            "area": round(sum(ring_area(r) for r in rings)),
        })
        detail[code] = to_path(rings, DETAIL_TOLERANCE, 0.3)

    common = {"viewBox": f"0 0 {WIDTH} {height:.0f}",
              "source": "Natural Earth 1:10m admin-1 (public domain), tools/region-map/build_map.py"}
    (WEB / "russia-map.json").write_text(
        json.dumps({**common, "subjects": subjects}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (WEB / "russia-map-detail.json").write_text(
        json.dumps({**common, "subjects": detail}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for name in ("russia-map.json", "russia-map-detail.json"):
        print(f"{name}: {(WEB / name).stat().st_size / 1024:.0f} КБ")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
