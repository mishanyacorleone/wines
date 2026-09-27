"""Разметка реальных фото: фото + поле «какое это вино».

Размечается верный ответ, а не выдача модели, поэтому разметка делается один
раз и подходит для сравнения любых прогонов. Ответы пишутся в файл на диске
после каждого изменения, браузерное хранилище не используется.

Запуск:  .venv/bin/python tools/labeler/label.py   →   http://127.0.0.1:8097
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def load_catalog(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                row = json.loads(line)
                rows.append({k: row.get(k) for k in ("slug", "title", "manufacturer", "category")})
    return rows


def option_label(row: dict) -> str:
    # slug в квадратных скобках в конце — по нему ответ однозначно
    # разбирается обратно, даже если названия у двух вин совпадают
    return f"{row['title']} — {row['manufacturer']} — {row['category']} [{row['slug']}]"


def build_page(photos: list[str], catalog: list[dict]) -> str:
    options = "\n".join(f'<option value="{html.escape(option_label(r))}">' for r in catalog)
    rows = "\n".join(
        f"""<section class="row" data-file="{html.escape(name)}">
  <a href="/photo/{html.escape(name)}" target="_blank"><img src="/photo/{html.escape(name)}" loading="lazy"></a>
  <div class="form">
    <div class="fname">{i}. {html.escape(name)}</div>
    <input class="wine" list="catalog" placeholder="начни вводить название или производителя">
    <label><input type="checkbox" class="absent"> нет в каталоге</label>
    <textarea class="note" rows="2" placeholder="заметка: что написано на этикетке, сомнения"></textarea>
    <div class="status"></div>
  </div>
</section>"""
        for i, name in enumerate(photos, start=1)
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Разметка вин</title>
<style>
  body {{ font: 14px system-ui, sans-serif; margin: 0; padding: 16px; background: #fff; color: #16161a; }}
  header {{ position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #ddd; z-index: 1; }}
  .row {{ display: flex; gap: 16px; padding: 16px 0; border-bottom: 1px solid #eee; flex-wrap: wrap; }}
  .row.done {{ background: #f1f8f1; }}
  .row img {{ height: 420px; max-width: 100%; object-fit: contain; background: #f4f4f6; border-radius: 8px; }}
  .form {{ display: flex; flex-direction: column; gap: 8px; flex: 1 1 320px; max-width: 640px; }}
  .fname {{ font-weight: 600; word-break: break-all; }}
  .wine {{ font: inherit; padding: 8px; }}
  textarea {{ font: inherit; padding: 6px; }}
  .status {{ color: #6b6b76; font-size: 12px; }}
  .warn {{ color: #b3261e; }}
</style></head><body>
<header><b>Разметка вин</b> · <span id="progress"></span></header>
<datalist id="catalog">{options}</datalist>
{rows}
<script>
const SLUG = /\\[([^\\]]+)\\]$/;
let labels = {{}};

function isDone(v) {{ return v && (v.absent || v.slug); }}

function progress() {{
  const done = Object.values(labels).filter(isDone).length;
  document.getElementById('progress').textContent =
    `размечено ${{done}} из {len(photos)} · файл: data/labels/real-photos.json`;
}}

function show(row, v) {{
  row.classList.toggle('done', !!isDone(v));
  const s = row.querySelector('.status');
  if (v && v.text && !v.slug && !v.absent) {{
    s.innerHTML = '<span class="warn">не выбрано из списка — сохранено как текст, slug нет</span>';
  }} else {{
    s.textContent = v && v.slug ? 'slug: ' + v.slug : '';
  }}
}}

async function save(row) {{
  const text = row.querySelector('.wine').value.trim();
  const m = text.match(SLUG);
  const v = {{
    slug: m ? m[1] : null,
    text: text,
    absent: row.querySelector('.absent').checked,
    note: row.querySelector('.note').value.trim(),
  }};
  labels[row.dataset.file] = v;
  const r = await fetch('/save', {{method: 'POST', body: JSON.stringify({{file: row.dataset.file, label: v}})}});
  show(row, v);
  if (!r.ok) row.querySelector('.status').innerHTML = '<span class="warn">не сохранилось!</span>';
  progress();
}}

(async () => {{
  labels = await (await fetch('/labels')).json();
  document.querySelectorAll('.row').forEach(row => {{
    const v = labels[row.dataset.file];
    if (v) {{
      row.querySelector('.wine').value = v.text || '';
      row.querySelector('.absent').checked = !!v.absent;
      row.querySelector('.note').value = v.note || '';
    }}
    show(row, v);
    row.querySelectorAll('input, textarea').forEach(el => el.addEventListener('change', () => save(row)));
  }});
  progress();
}})();
</script></body></html>"""


def make_handler(photos_dir: Path, labels_path: Path, page: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _labels(self) -> dict:
            if labels_path.exists():
                return json.loads(labels_path.read_text(encoding="utf-8"))
            return {}

        def do_GET(self) -> None:
            if self.path == "/":
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif self.path == "/labels":
                self._send(200, json.dumps(self._labels()).encode(), "application/json")
            elif self.path.startswith("/photo/"):
                path = (photos_dir / unquote(self.path[len("/photo/"):])).resolve()
                # не отдаём ничего за пределами папки с фото
                if path.parent != photos_dir.resolve() or not path.is_file():
                    self._send(404, b"not found", "text/plain")
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self._send(200, path.read_bytes(), content_type)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/save":
                self._send(404, b"not found", "text/plain")
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            labels = self._labels()
            labels[payload["file"]] = payload["label"]
            labels_path.parent.mkdir(parents=True, exist_ok=True)
            # через временный файл: оборванная запись не должна испортить
            # уже сделанную разметку
            tmp = labels_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(labels_path)
            self._send(200, b"ok", "text/plain")

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Разметка реальных фото вин")
    parser.add_argument("--photos-dir", type=Path, default=REPO_ROOT / "data" / "real-photos")
    parser.add_argument("--catalog", type=Path, default=REPO_ROOT / "data" / "catalog" / "catalog.jsonl")
    parser.add_argument("--labels", type=Path, default=REPO_ROOT / "data" / "labels" / "real-photos.json")
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()

    photos = sorted(
        p.name for p in args.photos_dir.iterdir()
        if p.suffix.lower() in IMAGE_SUFFIXES and not p.name.startswith("._")
    )
    page = build_page(photos, load_catalog(args.catalog))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.photos_dir, args.labels, page))
    print(f"Фото: {len(photos)} · разметка: {args.labels}\nОткрой http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
