"""HTML-отчёт для ручной разметки выдачи.

Ground truth организатор не выдаёт, поэтому единственный способ измерить
точность — посмотреть глазами. Отчёт кладёт фото запроса рядом с топ-5
кандидатами, даёт ссылку на карточку и позволяет отметить, на каком месте
оказался правильный ответ. Отметки хранятся в localStorage браузера,
метрики считаются на лету.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from PIL import Image

from .runner import QueryResult, RunSummary

logger = logging.getLogger(__name__)

QUERY_THUMB = (420, 560)
CANDIDATE_THUMB = (200, 280)


def make_thumb(source: Path, dest: Path, size: tuple[int, int]) -> bool:
    """Уменьшенная копия. Полноразмерные фото (3024x4032, ~1.5 МБ каждое)
    в отчёте на 100 запросов сделали бы страницу неоткрываемой."""
    if dest.exists():
        return True
    try:
        with Image.open(source) as image:
            image.load()
            if image.mode in ("RGBA", "LA", "P"):
                canvas = Image.new("RGB", image.size, (255, 255, 255))
                rgba = image.convert("RGBA")
                canvas.paste(rgba, mask=rgba.split()[-1])
                image = canvas
            else:
                image = image.convert("RGB")
            image.thumbnail(size)
            dest.parent.mkdir(parents=True, exist_ok=True)
            image.save(dest, format="JPEG", quality=82)
        return True
    except Exception as exc:
        logger.warning("Не сделал превью для %s: %s", source, exc)
        return False


def build_report(
    results: list[QueryResult],
    summary: RunSummary,
    *,
    queries_dir: Path,
    catalog_dir: Path,
    output_path: Path,
) -> Path:
    thumbs_dir = output_path.parent / "thumbs"
    rows: list[str] = []

    for index, result in enumerate(results):
        query_thumb = thumbs_dir / "queries" / f"{Path(result.filename).stem}.jpg"
        make_thumb(queries_dir / result.filename, query_thumb, QUERY_THUMB)

        cards: list[str] = []
        for candidate in result.results:
            image_path = candidate.get("image_path")
            thumb_src = full_src = None

            if image_path:
                source = catalog_dir / image_path
                candidate_thumb = thumbs_dir / "catalog" / f"{candidate['slug']}.jpg"
                if make_thumb(source, candidate_thumb, CANDIDATE_THUMB):
                    thumb_src = _rel(candidate_thumb, output_path)
                    full_src = _rel(source, output_path)

            cards.append(_candidate_card(candidate, thumb_src, full_src))

        rows.append(
            _query_row(
                index,
                result,
                _rel(query_thumb, output_path),
                _rel(queries_dir / result.filename, output_path),
                "".join(cards),
            )
        )

    document = _PAGE.format(
        stats=_summary_block(summary),
        rows="".join(rows),
        total=len(results),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return output_path


def _rel(path: Path, output_path: Path) -> str:
    import os

    return os.path.relpath(path, output_path.parent)


def _summary_block(summary: RunSummary) -> str:
    return f"""
    <div class="cards">
      <div class="card"><span>Фото</span><b>{summary.total}</b></div>
      <div class="card"><span>Ошибок запроса</span><b>{summary.failed}</b></div>
      <div class="card"><span>Общее время</span><b>{summary.wall_seconds:.1f} с</b></div>
      <div class="card"><span>Среднее</span><b>{summary.mean_ms:.0f} мс</b></div>
      <div class="card"><span>p50</span><b>{summary.percentile(50):.0f} мс</b></div>
      <div class="card"><span>p95</span><b>{summary.percentile(95):.0f} мс</b></div>
    </div>"""


def _candidate_card(candidate: dict, thumb_src: str | None, full_src: str | None) -> str:
    title = html.escape(candidate.get("title") or candidate["slug"])
    manufacturer = html.escape(candidate.get("manufacturer") or "")
    category = html.escape(candidate.get("category") or "")
    confidence = candidate.get("confidence", 0.0) * 100
    score = candidate.get("score", 0.0)
    url = html.escape(candidate.get("url", ""))

    # с VLM порядок задаёт его вероятность, а не косинус — показываем оба,
    # иначе непонятно, кто переставил кандидата
    details = f"cos {score:.4f}"
    if candidate.get("vlm_prob") is not None:
        details += f" · VLM {candidate['vlm_prob'] * 100:.0f}%"

    if thumb_src:
        # клик по превью открывает оригинал каталожного фото: этикетку видно
        # целиком, и для сверки не нужно уходить на сайт
        image = (
            f'<a href="{html.escape(full_src or thumb_src)}" target="_blank">'
            f'<img src="{html.escape(thumb_src)}" loading="lazy" alt="{title}"></a>'
        )
    else:
        image = '<div class="noimg">нет фото</div>'

    return f"""
      <div class="cand">
        <div class="rank">{candidate.get('rank')}</div>
        {image}
        <div class="meta">
          <a href="{url}" target="_blank" rel="noopener">{title}</a>
          <div class="sub">{manufacturer}</div>
          <div class="sub">{category}</div>
          <div class="score">conf {confidence:.1f}% · {details}</div>
        </div>
      </div>"""


def _query_row(index: int, result: QueryResult, thumb_src: str, full_src: str, cards: str) -> str:
    error = (
        f'<div class="error">Ошибка: {html.escape(result.error)}</div>'
        if result.error
        else ""
    )
    server = result.server_timings or {}
    timings = (
        f"{result.latency_ms:.0f} мс всего · энкодер {server.get('encode_ms', 0):.0f}"
        f" · поиск {server.get('search_ms', 0):.0f}"
    )
    if server.get("vlm_ms"):
        timings += f" · VLM {server['vlm_ms']:.0f}"
    # решение сервиса: «нашли» или «нет в каталоге» и по какому сигналу
    verdict = ""
    if result.status:
        verdict = f"решение: {html.escape(result.status)} ({html.escape(result.reason or '')})"
        if result.verify_prob is not None:
            verdict += f" · проверка {result.verify_prob * 100:.0f}%"
        if result.vlm_none_prob is not None:
            verdict += f" · «ни одна» {result.vlm_none_prob * 100:.0f}%"
        verdict = f'<div class="sub">{verdict}</div>'
    return f"""
    <section class="row" data-index="{index}">
      <div class="query">
        <a href="{html.escape(full_src)}" target="_blank"><img src="{html.escape(thumb_src)}" loading="lazy" alt=""></a>
        <div class="qmeta">
          <div class="fname">{html.escape(result.filename)}</div>
          <div class="sub">{timings}</div>
          <div class="sub">margin {result.margin:.4f} · top1 {result.top1_confidence * 100:.1f}%</div>
          {verdict}
          {error}
          <div class="marks">
            <label><input type="radio" name="m{index}" value="1"> верно #1</label>
            <label><input type="radio" name="m{index}" value="2"> в топ-5</label>
            <label><input type="radio" name="m{index}" value="0"> нет в выдаче</label>
            <label><input type="radio" name="m{index}" value="x"> не в каталоге</label>
          </div>
        </div>
      </div>
      <div class="cands">{cards}</div>
    </section>"""


_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Разметка выдачи сканера вин</title>
<style>
  :root {{ color-scheme: light dark; --bg:#fff; --fg:#16161a; --muted:#6b6b76;
           --line:#e4e4ea; --panel:#f7f7f9; --accent:#8a1538; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141416; --fg:#ececf1; --muted:#9a9aa5; --line:#2a2a30;
             --panel:#1c1c20; --accent:#e0607e; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:24px 16px 80px; background:var(--bg); color:var(--fg);
          font:15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .lead {{ color:var(--muted); margin:0 0 20px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:10px 14px; min-width:110px; }}
  .card span {{ display:block; color:var(--muted); font-size:12px; }}
  .card b {{ font-size:19px; }}
  #tally {{ position:sticky; top:0; z-index:5; background:var(--bg);
            border-bottom:1px solid var(--line); padding:10px 0; margin-bottom:12px; }}
  .row {{ display:flex; gap:18px; padding:18px 0; border-top:1px solid var(--line);
          align-items:flex-start; flex-wrap:wrap; }}
  .query {{ display:flex; gap:12px; min-width:320px; flex:0 0 auto; }}
  .query img {{ width:180px; border-radius:8px; background:var(--panel); }}
  .qmeta {{ max-width:240px; }}
  .fname {{ font-weight:600; word-break:break-all; font-size:13px; }}
  .sub {{ color:var(--muted); font-size:12px; }}
  .error {{ color:#c0392b; font-size:12px; margin-top:6px; }}
  .marks {{ display:flex; flex-direction:column; gap:3px; margin-top:10px; font-size:12px; }}
  .cands {{ display:flex; gap:12px; flex:1 1 400px; overflow-x:auto; padding-bottom:4px; }}
  .cand {{ position:relative; width:150px; flex:0 0 auto; }}
  .cand img {{ width:150px; height:190px; object-fit:contain; background:var(--panel);
               border:1px solid var(--line); border-radius:8px; }}
  .noimg {{ width:150px; height:190px; display:grid; place-items:center;
             background:var(--panel); border:1px dashed var(--line); border-radius:8px;
             color:var(--muted); font-size:12px; }}
  .rank {{ position:absolute; top:4px; left:4px; background:var(--accent); color:#fff;
           width:20px; height:20px; border-radius:50%; display:grid; place-items:center;
           font-size:11px; font-weight:700; }}
  .meta a {{ color:var(--fg); font-weight:600; font-size:12px; display:block;
             margin-top:6px; text-decoration:none; }}
  .meta a:hover {{ color:var(--accent); text-decoration:underline; }}
  .score {{ color:var(--muted); font-size:11px; margin-top:3px; }}
  button {{ font:inherit; padding:6px 12px; border-radius:8px; border:1px solid var(--line);
            background:var(--panel); color:var(--fg); cursor:pointer; }}
</style>
</head>
<body>
<h1>Разметка выдачи сканера вин</h1>
<p class="lead">Открой ссылку кандидата и сравни этикетку с фото запроса. Отметки
сохраняются в браузере, метрики считаются сразу.</p>
{stats}
<div id="tally"></div>
{rows}
<script>
  const TOTAL = {total};
  const KEY = 'wine-eval-marks';
  const marks = JSON.parse(localStorage.getItem(KEY) || '{{}}');

  function render() {{
    const values = Object.values(marks);
    const scored = values.filter(v => v !== 'x');
    const top1 = scored.filter(v => v === '1').length;
    const top5 = scored.filter(v => v === '1' || v === '2').length;
    const n = scored.length || 1;
    document.getElementById('tally').innerHTML =
      `<b>Размечено ${{values.length}} из ${{TOTAL}}</b> (вне каталога: ${{values.length - scored.length}}) &nbsp;·&nbsp; ` +
      `top-1: <b>${{top1}}/${{scored.length}} = ${{(top1 / n * 100).toFixed(1)}}%</b> &nbsp;·&nbsp; ` +
      `top-5: <b>${{top5}}/${{scored.length}} = ${{(top5 / n * 100).toFixed(1)}}%</b> ` +
      `<button onclick="exportMarks()">Выгрузить JSON</button> ` +
      `<button onclick="resetMarks()">Сбросить</button>`;
  }}

  function exportMarks() {{
    const blob = new Blob([JSON.stringify(marks, null, 2)], {{type: 'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'marks.json';
    a.click();
  }}

  function resetMarks() {{
    if (!confirm('Удалить все отметки?')) return;
    localStorage.removeItem(KEY);
    location.reload();
  }}

  document.querySelectorAll('input[type=radio]').forEach(input => {{
    const index = input.name.slice(1);
    if (marks[index] === input.value) input.checked = true;
    input.addEventListener('change', () => {{
      marks[index] = input.value;
      localStorage.setItem(KEY, JSON.stringify(marks));
      render();
    }});
  }});

  render();
</script>
</body>
</html>
"""
