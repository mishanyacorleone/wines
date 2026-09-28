"""Сводная таблица точности всех прогонов: data/reports/accuracy.xlsx.

    PYTHONPATH=tools/eval-runner/src .venv/bin/python -m eval_runner.excel

По листу на прогон (выдача топ-5 против ручной разметки) и лист «Итог»:
точность считается формулами из листов прогонов, так что правка разметки
прямо в таблице сразу меняет итог. Время и отказ «нет в каталоге» — числами,
посчитанными здесь же.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .labels import ABSENT, DEFAULT_LABELS, SKIP, SURE, UNSURE, load_labels

REPO_ROOT = Path(__file__).resolve().parents[4]
REPORTS = REPO_ROOT / "data" / "reports"

STATUS_NAMES = {SURE: "уверенно", UNSURE: "не уверен", ABSENT: "нет в каталоге", SKIP: "исключено"}
HEADER_FILL = PatternFill("solid", fgColor="FFDDDDDD")
MATCH_FILL = PatternFill("solid", fgColor="FFC6EFCE", bgColor="FFC6EFCE")
BOLD = Font(bold=True)


@dataclass(frozen=True)
class Run:
    sheet: str
    path: Path
    ocr: str
    vlm_input: str
    images: str
    note: str = ""


RUNS = [
    Run("Без OCR", REPORTS / "results.jsonl", "нет", "—", "—"),
    Run("С OCR", REPORTS / "ocr" / "results.jsonl", "да", "—", "—"),
    Run("VLM текст", REPORTS / "vlm-text" / "results.jsonl", "да", "текст карточек", "1 Мп"),
    Run("VLM текст+фото", REPORTS / "vlm-images" / "results.jsonl",
        "да", "текст + эталонные фото", "1 Мп / 256k"),
    Run("VLM текст+фото, малые", REPORTS / "vlm-images-small" / "results.jsonl",
        "да", "текст + эталонные фото", "512k / 128k"),
    Run("VLM без OCR", REPORTS / "vlm-images-noocr" / "results.jsonl",
        "нет", "текст + эталонные фото", "1 Мп / 256k"),
    Run("VLM без OCR, малые", REPORTS / "vlm-images-noocr-small" / "results.jsonl",
        "нет", "текст + эталонные фото", "512k / 128k", "лучший вариант"),
    Run("Сервис VLM", REPORTS / "service-vlm" / "results.jsonl",
        "нет", "текст + эталонные фото", "512k / 128k",
        "тот же вариант, встроенный в сервис: WINE_VLM_ENABLED=1, WINE_OCR_ENABLED=0"),
    Run("Сервис VLM топ-10", REPORTS / "service-vlm-top10" / "results.jsonl",
        "нет", "текст + эталонные фото, топ-10, метки A–J", "512k / 128k",
        "VLM выбирает из топ-10 + проверка «нет в каталоге»; отклонён: медленнее и хуже"),
    Run("Сервис VLM топ-5, A–E", REPORTS / "service-vlm5-top10" / "results.jsonl",
        "нет", "текст + эталонные фото, топ-5 из топ-10", "512k / 128k",
        "наружу топ-10, VLM выбирает из топ-5 (метки A–E), проверка «нет в каталоге»"),
    Run("Сервис итог", REPORTS / "service-final" / "results.jsonl",
        "нет", "текст + эталонные фото, топ-5 из топ-10", "512k / 128k",
        "итоговый пайплайн: метки 1–5, проверка «нет в каталоге», {\"slug\": null} для вин вне каталога"),
]

# Колонки листа прогона; буквы используются в формулах «Итога»
COL_STATUS, COL_RANK, COL_TOP1, COL_TOP5 = "B", "K", "L", "M"


def _describe(wine: dict | None) -> str:
    if not wine:
        return ""
    parts = [wine.get("title"), wine.get("manufacturer"), wine.get("category")]
    return " — ".join(p for p in parts if p)


def _load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file]


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(int(0.95 * len(ordered)), len(ordered) - 1)]


def _header(ws, titles: list[str], widths: dict[str, float]) -> None:
    ws.append(titles)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


def _run_sheet(wb: Workbook, run: Run, rows: list[dict], labels, catalog: dict) -> int:
    ws = wb.create_sheet(run.sheet)
    has_vlm = any("vlm_none_prob" in r and r["vlm_none_prob"] is not None for r in rows)
    titles = [
        "Фото", "Статус разметки", "target", "target: вино", "Комментарий разметчика",
        "top-1", "top-2", "top-3", "top-4", "top-5",
        "Ранг target", "Верно top-1", "Верно top-5",
        "top-1: вино", "top-2: вино", "top-3: вино", "top-4: вино", "top-5: вино",
    ]
    if has_vlm:
        titles += ["top-1: вероятность VLM", "«нет в списке» (VLM)", "VLM, мс"]
    _header(ws, titles, {"A": 30, "B": 14, "C": 34, "D": 40, "E": 30, "F": 34, "G": 34,
                         "H": 34, "I": 34, "J": 34, "K": 9, "L": 9, "M": 9, "N": 40,
                         "O": 40, "P": 40, "Q": 40, "R": 40, "S": 12, "T": 12, "U": 10})

    for number, row in enumerate(sorted(rows, key=lambda r: r["filename"]), start=2):
        label = labels.get(row["filename"])
        target = label.slugs[0] if label and label.slugs else None
        top = row.get("results", [])[:5]
        slugs = [c["slug"] for c in top] + [None] * (5 - len(top))
        line = [
            row["filename"],
            STATUS_NAMES.get(label.status, "") if label else "",
            target,
            _describe(catalog.get(target)) if target else None,
            (label.note or None) if label else None,
            *slugs,
            f'=IF(C{number}="","",IFERROR(MATCH(C{number},F{number}:J{number},0),"нет"))',
            f'=IF(C{number}="","",IF(K{number}=1,1,0))',
            f'=IF(C{number}="","",IF(ISNUMBER(K{number}),1,0))',
            *[_describe(c) for c in top], *[None] * (5 - len(top)),
        ]
        if has_vlm:
            line += [
                top[0].get("vlm_prob") if top else None,
                row.get("vlm_none_prob"),
                row.get("vlm_ms") or row.get("server_timings", {}).get("vlm_ms"),
            ]
        ws.append(line)
        if has_vlm:
            ws[f"S{number}"].number_format = "0.0%"
            ws[f"T{number}"].number_format = "0.0%"

    last = ws.max_row
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{last}"
    ws.conditional_formatting.add(
        f"F2:J{last}", FormulaRule(formula=['AND($C2<>"",F2=$C2)'], fill=MATCH_FILL)
    )
    return last


def _latency(rows: list[dict]) -> tuple[str, str, str]:
    """Медиана и p95 VLM и оценка p95 полного ответа сервиса, мс."""
    vlm = [r.get("vlm_ms") or r.get("server_timings", {}).get("vlm_ms") for r in rows]
    vlm = [v for v in vlm if v]
    if any("vlm_ms" in r.get("server_timings", {}) for r in rows) or not vlm:
        # прогон через сервис: клиентское время уже включает всё
        total = [r["latency_ms"] for r in rows if not r.get("error")]
    else:
        # офлайн-прогон: время сервиса до VLM + сам VLM
        total = [r["server_timings"].get("total_ms", 0) + (r.get("vlm_ms") or 0) for r in rows]
    vlm_median = f"{statistics.median(vlm):.0f}" if vlm else "—"
    vlm_p95 = f"{_p95(vlm):.0f}" if vlm else "—"
    return vlm_median, vlm_p95, f"{_p95(total):.0f}"


def _refusals(rows: list[dict], labels, threshold: float = 0.5) -> tuple[str, str]:
    if not any(r.get("vlm_none_prob") is not None for r in rows):
        return "—", "—"
    absent = [r for r in rows if labels.get(r["filename"]) and labels[r["filename"]].status == ABSENT]
    known = [r for r in rows if labels.get(r["filename"])
             and labels[r["filename"]].status in (SURE, UNSURE)]
    caught = sum((r.get("vlm_none_prob") or 0) > threshold for r in absent)
    false = sum((r.get("vlm_none_prob") or 0) > threshold for r in known)
    return f"{caught} из {len(absent)}", f"{false} из {len(known)}"


def _summary(ws, runs: list[tuple[Run, int, list[dict]]], labels) -> None:
    notes = [
        "Как считается",
        "target — верный slug из ручной разметки (Label Studio → data/labels/real-photos.json).",
        "top-1…top-5 — выдача на том же фото. Ранг target = MATCH(target; top-1:top-5).",
        "Верно top-1 = target на 1-м месте; верно top-5 = target в первой пятёрке. "
        "Фото без target (нет в каталоге, исключено) не считаются.",
        "VLM (Qwen3-VL-4B) переставляет первые 5 кандидатов картинки/OCR, поэтому top-5 "
        "у VLM-прогонов тот же, что у базы, а top-1 — выбор VLM. Пересобрать таблицу: "
        "python -m eval_runner.excel.",
    ]
    for text in notes:
        ws.append([text])
    ws["A1"].font = BOLD
    ws.append([])

    ws.append(["Набор", "Конфигурация", "Фото", "Верно top-1", "top-1", "Верно top-5", "top-5"])
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = BOLD
        cell.fill = HEADER_FILL

    for group, statuses in (("уверенно", ["уверенно"]),
                            ("уверенно + не уверен", ["уверенно", "не уверен"])):
        for run, last, _ in runs:
            sheet = f"'{run.sheet}'"
            status = f"{sheet}!${COL_STATUS}$2:${COL_STATUS}${last}"

            def count(extra: str = "") -> str:
                return "+".join(f'COUNTIFS({status},"{s}"{extra})' for s in statuses)

            top1 = f",{sheet}!${COL_TOP1}$2:${COL_TOP1}${last},1"
            top5 = f",{sheet}!${COL_TOP5}$2:${COL_TOP5}${last},1"
            n = ws.max_row + 1
            ws.append([group, run.sheet, f"={count()}", f"={count(top1)}",
                       f"=IF(C{n}=0,0,D{n}/C{n})", f"={count(top5)}", f"=IF(C{n}=0,0,F{n}/C{n})"])
            ws[f"E{n}"].number_format = "0.0%"
            ws[f"G{n}"].number_format = "0.0%"
            if run.note == "лучший вариант":
                for cell in ws[n]:
                    cell.font = BOLD

    ws.append([])
    base = f"'{runs[1][0].sheet}'!${COL_STATUS}$2:${COL_STATUS}${runs[1][1]}"
    ws.append(["Фото «нет в каталоге»", None, f'=COUNTIFS({base},"нет в каталоге")'])
    ws.append(["Исключено (две бутылки в кадре)", None, f'=COUNTIFS({base},"исключено")'])

    ws.append([])
    ws.append(["Конфигурация", "OCR", "Вход VLM", "Картинки VLM (фото / эталон)",
               "VLM, медиана мс", "VLM, p95 мс", "Ответ, p95 мс",
               "Отказ «нет в каталоге» (P>0,5)", "Ложные отказы", "Файл", "Примечание"])
    for cell in ws[ws.max_row]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for run, _, rows in runs:
        vlm_median, vlm_p95, total_p95 = _latency(rows)
        caught, false = _refusals(rows, labels)
        ws.append([run.sheet, run.ocr, run.vlm_input, run.images, vlm_median, vlm_p95,
                   total_p95, caught, false, str(run.path.relative_to(REPO_ROOT)), run.note])
    ws.append([])
    ws.append(["«Ответ, p95» у офлайн-прогонов — время сервиса до VLM плюс сам VLM; "
               "у «Сервис VLM» — замер клиентом, как у скрипта организатора. SLA — 3 с."])

    for column, width in {"A": 26, "B": 22, "C": 24, "D": 16, "E": 14, "F": 12, "G": 12,
                          "H": 18, "I": 14, "J": 44, "K": 40}.items():
        ws.column_dimensions[column].width = width


def build(runs: list[Run], labels_path: Path, catalog_path: Path, out: Path) -> Path:
    labels = load_labels(labels_path)
    catalog = {}
    with catalog_path.open(encoding="utf-8") as file:
        for line in file:
            wine = json.loads(line)
            catalog[wine["slug"]] = wine

    wb = Workbook()
    summary = wb.active
    summary.title = "Итог"
    built = []
    for run in runs:
        if not run.path.exists():
            print(f"пропущен {run.sheet}: нет {run.path}", file=sys.stderr)
            continue
        rows = _load_rows(run.path)
        last = _run_sheet(wb, run, rows, labels, catalog)
        built.append((run, last, rows))
    _summary(summary, built, labels)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сводная таблица точности прогонов")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--catalog", type=Path, default=REPO_ROOT / "data/catalog/catalog.jsonl")
    parser.add_argument("--out", type=Path, default=REPORTS / "accuracy.xlsx")
    args = parser.parse_args(argv)
    print(build(RUNS, args.labels, args.catalog, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
