"""Сопоставление текста с этикетки с метаданными кандидатов.

OCR на фото с телефона шумный: «Л» в стилизованном шрифте читается как «А»
(«МУСКАТЕАЬ»), слова рвутся пробелом («СИР А»), кириллица перемешивается
с похожей латиницей («KАБЕ₽HЕ»), в кадр попадает текст соседних бутылок.
Поэтому сравнение нечёткое, и оценивается не «весь ли текст совпал», а какие
слова кандидата удалось найти на этикетке — с весом по редкости слова.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from rapidfuzz import fuzz, process

# Латиница, которую OCR подставляет вместо похожей кириллицы. Применяется
# только к словам, где уже есть кириллица: чисто латинские названия
# (Purity, Syrah) должны остаться латиницей.
_LOOKALIKE_UPPER = str.maketrans("ABCEHKMOPTXY₽", "АВСЕНКМОРТХУР")
_LOOKALIKE_LOWER = str.maketrans("aceopxy", "асеорху")

# Кириллица -> латиница: единое пространство для сравнения, чтобы слово,
# целиком прочитанное похожими латинскими буквами («BEC»), совпало с «ВЕС»
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya",
})

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_WORD = re.compile(r"[^\W_]+")

# Окончания прилагательных: «белое» на карточке и «белый» на этикетке —
# одно и то же слово. Проверяются от длинных к коротким.
_ENDINGS = ("ого", "его", "ое", "ый", "ий", "ая", "яя", "ые", "ие", "ой", "ей", "ую")

# Слова, которые есть почти на каждой этикетке и ничего не различают
_STOPWORDS = {
    "вино", "вина", "винодельня", "россии", "россия", "российское",
    "wine", "winery", "the", "and",
}

# Латинские термины категорий. Без приведения «EXTRA BRUT» на этикетке
# считался редким словом (по-латински он в каталоге почти не встречается)
# и вытаскивал любое вино с латинским «Brut» в названии
_SYNONYMS = {"brut": "брют", "extra": "экстра", "rose": "розовое", "rosé": "розовое"}

_MIN_TOKEN_LEN = 3


def _normalize(word: str) -> str:
    if _CYRILLIC.search(word):
        word = word.translate(_LOOKALIKE_UPPER)
        word = word.lower().translate(_LOOKALIKE_LOWER)
    word = word.lower().replace("ё", "е")
    return _SYNONYMS.get(word, word)


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_TOKEN_LEN:
            return word[: -len(ending)]
    return word


def _key(word: str) -> str:
    """Форма для нечёткого сравнения: латиница без регистра."""
    return word.translate(_TRANSLIT)


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _WORD.findall(text or ""):
        word = _normalize(raw)
        if len(word) >= _MIN_TOKEN_LEN and word not in _STOPWORDS:
            tokens.append(word)
    return tokens


def payload_tokens(payload: dict[str, Any]) -> list[str]:
    """Слова кандидата, которые имеет смысл искать на этикетке."""
    parts = [
        payload.get("title"),
        payload.get("manufacturer"),
        payload.get("category"),
        *(payload.get("grapes") or []),
    ]
    return list(dict.fromkeys(t for part in parts if part for t in tokenize(part)))


def ocr_tokens(lines: Iterable[str]) -> list[str]:
    """Слова с этикетки плюс склейки: OCR рвёт слова пробелом («СИР А»)."""
    tokens: list[str] = []
    for line in lines:
        words = [_normalize(w) for w in _WORD.findall(line)]
        tokens.extend(w for w in words if len(w) >= _MIN_TOKEN_LEN)
        tokens.extend(a + b for a, b in zip(words, words[1:]))
        if len(words) > 2:
            tokens.append("".join(words))
    return list(dict.fromkeys(tokens))


# Цвет и сладость — закрытые словари. Если этикетка явно говорит
# «полусладкое», а у кандидата «сухое», это не отсутствие улики, а
# противоречие: у вин одной линейки бутылка одинаковая, и различает их
# именно это слово.
_CATEGORY_GROUPS = (
    ("белое", "красное", "розовое", "оранжевое"),
    ("сухое", "полусухое", "полусладкое", "сладкое", "брют", "десертное"),
)


@dataclass
class TextScore:
    score: float
    matched: list[str]
    conflicts: list[str]


class TextMatcher:
    """Оценивает кандидатов по словам, прочитанным с этикетки.

    Балл — сумма IDF найденных слов, а не их доля: доля награждала бы
    кандидатов просто за короткое описание. Слова, общие для всей линейки
    (производитель), дают всем кандидатам одинаковую прибавку и порядок
    внутри линейки не меняют — его решают различающие слова.
    """

    def __init__(
        self,
        payloads: Iterable[dict[str, Any]],
        *,
        min_ratio: float = 80.0,
        evidence_cap: float = 40.0,
        conflict_penalty: float = 0.15,
    ) -> None:
        documents = [set(payload_tokens(p)) for p in payloads]
        counts = Counter(token for doc in documents for token in doc)
        total = len(documents)
        # Сглаженный IDF: сорт, встречающийся у сотни вин («шардоне»), весит
        # меньше, чем название линейки, которое есть у двух
        self._idf = {
            token: math.log((total + 1) / (count + 1)) + 1.0 for token, count in counts.items()
        }
        self._default_idf = math.log(total + 1) + 1.0
        self._min_ratio = min_ratio
        # ~пять редких слов дают максимум. Потолок ниже (20) обрезал различающее
        # слово: у вин одной линейки общих слов много, оба кандидата упирались в
        # 1.0, и найденное «полусладкое» ничего не решало
        self._evidence_cap = evidence_cap
        self._conflict_penalty = conflict_penalty

    def __len__(self) -> int:
        return len(self._idf)

    def prepare(self, lines: Iterable[str]) -> "OcrEvidence":
        """Разбирает OCR один раз на запрос, а не на каждого кандидата."""
        tokens = ocr_tokens(lines)
        # Точное совпадение основы — только для целых слов: обрывок «бел:»
        # с края кадра иначе совпал бы с «белое» у любого кандидата
        stems = {_stem(t) for t in tokens if len(t) > _MIN_TOKEN_LEN}
        evidence = OcrEvidence(tokens, [_key(t) for t in tokens], stems)
        evidence.categories = [
            {term for term in group if self._found(term, evidence)} for group in _CATEGORY_GROUPS
        ]
        return evidence

    def _found(self, token: str, ocr: "OcrEvidence") -> bool:
        if token.isdigit():
            # цифры (год, номер) — только точное совпадение, иначе 2023 ≈ 2025
            return token in ocr.tokens
        if _stem(token) in ocr.stems:
            return True
        # короткое слово с одной ошибкой — уже другое слово
        if len(token) < 5 or not ocr.keys:
            return False
        best = process.extractOne(_key(token), ocr.keys, scorer=fuzz.ratio)
        return best is not None and best[1] >= self._min_ratio

    def _dedupe(self, tokens: list[str]) -> list[str]:
        """Одно слово в двух написаниях — одна улика.

        «Fanagoria» и «Фанагория», «Golubitskoe» и «Голубицкое»: без этого
        кандидат, у которого бренд записан обоими алфавитами, получал двойной
        вес и обгонял соседей по линейке только за счёт написания.
        """
        kept: list[str] = []
        for token in tokens:
            key = _key(token)
            if not any(fuzz.ratio(key, _key(k)) >= self._min_ratio for k in kept):
                kept.append(token)
        return kept

    def score(self, payload: dict[str, Any], ocr: "OcrEvidence") -> TextScore:
        """Балл в [-1, 1]: улики за кандидата минус противоречия."""
        if not ocr.tokens:
            return TextScore(0.0, [], [])

        matched = self._dedupe([t for t in payload_tokens(payload) if self._found(t, ocr)])
        evidence = sum(self._idf.get(t, self._default_idf) for t in matched)

        category = set(tokenize(payload.get("category") or ""))
        # «Мускатель белый»: цвет назван в самом названии, и если он найден
        # на этикетке, то красное с соседней бутылки — не противоречие
        matched_stems = {_stem(t) for t in matched}
        conflicts, conflicting_groups = [], 0
        for group, seen in zip(_CATEGORY_GROUPS, ocr.categories):
            own = category.intersection(group)
            if any(_stem(term) in matched_stems for term in own):
                continue
            # противоречие — только если этикетка назвала термин группы,
            # а у кандидата в этой группе другой. Штраф — за группу, а не за
            # термин: шум с соседней бутылки не должен его удваивать
            if seen and own and not own & seen:
                conflicts.extend(sorted(seen))
                conflicting_groups += 1

        score = min(evidence / self._evidence_cap, 1.0)
        score -= self._conflict_penalty * conflicting_groups
        return TextScore(max(score, -1.0), matched, conflicts)


@dataclass
class OcrEvidence:
    tokens: list[str]
    keys: list[str]
    stems: set[str]
    categories: list[set[str]] | None = None
