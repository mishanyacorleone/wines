# MVP: CLIP + Qdrant + OCR

> **Исторический документ — исходный план до реализации.** MVP реализован, и
> реализация отличается от плана: энкодер — SigLIP 2, а не CLIP; OCR подключён
> как переранжирование топ-20; добавлен VLM-реранкер топ-5. Как устроено сейчас →
> `ARCHITECTURE.md`, цифры → `baseline-results.md`, история → `changelog.md`.

Исходный план по шагам, основанный на решениях, принятых в ходе обсуждения (см. `CLAUDE.md` за контекстом задачи).

## Общая идея (напоминание)

Это retrieval, не классификация:
1. Строим индекс эмбеддингов один раз по всему каталогу.
2. На каждый запрос — превращаем фото в вектор, ищем ближайшего соседа в индексе, возвращаем его `slug`.
3. Новые вина (~50/день) — просто `upsert` нового вектора в индекс, без переобучения энкодера.

## Шаг 1: собрать финальный каталог `(image_path, slug, metadata)`

Источники (в порядке приоритета):
1. Матченные пары CSV↔архив (1652 вина) — см. `catalog-matching.md`.
2. Результат парсинга vino-svoe.ru (см. `scraper.md`) — закрывает оставшиеся пробелы и/или используется как более надёжный источник целиком (решить, что предпочтительнее: архивные фото или свежие с сайта, если оба варианта есть для одного вина).

Результат шага — единая таблица (например `final_catalog.csv`):
```
slug, image_path, title, region, manufacturer, category, color, description
```

Важно учесть при сборке:
- Дедуплицировать по `slug` (один вход на вино).
- Для 13 вин с несколькими фото — решить: использовать оба (несколько эмбеддингов на один slug в индексе) или один. Несколько эмбеддингов на slug — предпочтительнее, если retrieval-логика это поддерживает (Qdrant это умеет: несколько точек с одинаковым payload `slug`).

## Шаг 2: построить эмбеддинги через CLIP

Взять готовую модель без дообучения (zero-shot) для первого прохода:
- `openai/clip-vit-base-patch32` (через `transformers`) или
- `open_clip` `ViT-B-32` (через `open_clip_torch`)

Псевдокод:
```python
from PIL import Image
import torch
import clip  # или open_clip

model, preprocess = clip.load("ViT-B/32", device="cuda" if torch.cuda.is_available() else "cpu")

def get_embedding(image_path: str) -> np.ndarray:
    image = preprocess(Image.open(image_path)).unsqueeze(0).to(device)
    with torch.no_grad():
        embedding = model.encode_image(image)
    return embedding.cpu().numpy().flatten()
```

Прогнать по всему финальному каталогу, сохранить эмбеддинги (например, в `.npy` или сразу в Qdrant, минуя промежуточный файл).

## Шаг 3: индексация в Qdrant

- Коллекция: например `wines`.
- Размерность вектора — зависит от модели (для `ViT-B/32` — 512).
- Payload на каждую точку: `{"slug": "...", "title": "...", ...}` — чтобы после поиска сразу получить нужные метаданные, без дополнительного похода в таблицу.
- Метрика: косинусное сходство (`Cosine`).

Псевдокод:
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

client = QdrantClient(":memory:")  # или локальный/удалённый инстанс

client.create_collection(
    collection_name="wines",
    vectors_config=VectorParams(size=512, distance=Distance.COSINE),
)

points = [
    PointStruct(id=i, vector=embedding.tolist(), payload={"slug": slug, "title": title})
    for i, (embedding, slug, title) in enumerate(zip(embeddings, slugs, titles))
]
client.upsert(collection_name="wines", points=points)
```

Добавление нового вина (~50/день, без переобучения):
```python
new_embedding = get_embedding(new_image_path)
client.upsert(
    collection_name="wines",
    points=[PointStruct(id=next_id, vector=new_embedding.tolist(), payload={"slug": new_slug})],
)
```

## Шаг 4: поиск (inference)

```python
def predict_slug(query_image_path: str, top_k: int = 5) -> str:
    embedding = get_embedding(query_image_path)
    results = client.search(collection_name="wines", query_vector=embedding.tolist(), limit=top_k)
    return results[0].payload["slug"]  # top-1
```

## Шаг 5: первая проверка — 3 тестовых query-фото

Прогнать `predict_slug` на 3 фото из `eval.zip/queries/`. Ground truth нет — проверка глазами:
- Достаточно разумно ли выглядит найденное вино (визуально похоже на запрос)?
- Если организатор прислал доп. 100 query-фото — прогнать и на них, тоже глазами оценить долю разумных попаданий.

Подробнее про то, как вообще тестировать без ground truth → `testing-and-validation.md`.

## Шаг 6 (запасной): OCR как дополнительный сигнал

Не часть строгого MVP, но предусмотреть, если zero-shot CLIP окажется недостаточно точным на реалистичных фото (domain gap каталог vs полка магазина).

Идея: этикетки вина часто содержат уникальный текст (название, год, винодельня) — можно использовать как:
- **Fallback**, если top-1 CLIP-кандидат имеет низкую уверенность (низкое косинусное сходство) — тогда пробуем сузить кандидатов через OCR-текст.
- **Re-ranking сигнал**: взять top-k кандидатов от CLIP, для каждого сравнить OCR-текст запроса с текстовыми метаданными (`title`, `manufacturer`) кандидата — например, через fuzzy string matching — и переранжировать.

Инструменты для OCR на русском тексте этикеток: `EasyOCR` (поддерживает русский), Tesseract с `rus` traineddata, или облачные API (Yandex Vision OCR — учитывая, что данные и так российские).

Это отдельная веха, реализовать после того, как будет ясна базовая точность чистого CLIP-подхода — если её достаточно, OCR можно не делать вообще ради простоты MVP (`Prefers simple, working solutions over complex workarounds`).

## Шаг 7 (после MVP): обёртка в HTTP-сервис

Под контракт организатора (см. `CLAUDE.md`):
- `POST /v1/eval/predict`, `multipart/form-data`, поле `image`
- Ответ `{"slug": "..."}`
- Уложиться в 10 сек на запрос — не забыть, что модель должна быть уже загружена в память при старте сервиса, не грузиться на каждый запрос заново.

Не начато, отдельная задача после того, как сам pipeline (шаги 1-5) подтвердит вменяемую точность.