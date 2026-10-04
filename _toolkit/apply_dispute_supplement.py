#!/usr/bin/env python3
"""Дополняет строки карты конфликтов текстом из отчётов `dispute-supplement`.

Владелец 2026-09-16: находка, которая спорит со стоящим спором, не отклоняется, а дополняет его
строку — аргументацией, углом, примерами нового источника (канон: `SCHEMA.md`, «Язык описания
расхождений»). Скрипт приписывает присланный текст к выбранной ячейке строки, не переписывая
существующее, и объявляет о каждом нарушении вслух, а не втихую.

    python3 _toolkit/apply_dispute_supplement.py --wiki . --write _staging/stance/supplement-part-*.json

Правила приёмки текста (нарушившее не применяется):
  * без `**`, переводов строк, цепочек «→» и «->», перечислений через слэш;
  * 80–600 знаков и хотя бы одна точка — это полные предложения, а не обрывок;
  * `sources:` шапки карты получает источник, если он ещё не объявлен.
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolkit

COLS = ("Позиция A", "Позиция B", "Статус", "Когда выигрывает A", "Когда выигрывает B",
        "Чем подтверждено", "Спор", "#")


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def write(p, t):
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(t)


def reject_text(text):
    """Причины, по которым текст не принимается."""
    bad = []
    if len(text) < 80 or len(text) > 600:
        bad.append("длина %d знаков (нужно 80–600)" % len(text))
    if "**" in text:
        bad.append("двойные звёздочки")
    if "\n" in text:
        bad.append("перевод строки внутри текста")
    if "→" in text or "->" in text:
        bad.append("цепочка со стрелкой")
    if re.search(r"\w\s*/\s*\w", text) and not re.search(r"https?://", text):
        bad.append("перечисление через слэш")
    if "." not in text:
        bad.append("нет ни одной точки: это не предложение")
    return bad


def load_table(text):
    """Строки таблицы карты: (номер строки файла, номер спора, ячейки)."""
    rows, header_idx = [], None
    for i, line in enumerate(text.split("\n")):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header_idx is None and any(c == "Позиция A" for c in cells):
            header_idx = i
            header = cells
            continue
        m = re.match(r"^\**(\d+)\**$", cells[0])
        if header_idx is not None and m and len(cells) >= 6:
            rows.append((i, m.group(1), cells))
    return rows, header


# Короткие имена колонок, которыми пишут подагенты, и их полные заголовки в карте.
ALIASES = {
    "чем подтверждено": "Чем подтверждено и куда движется",
    "когда выигрывает a": "Когда выигрывает позиция A",
    "когда выигрывает b": "Когда выигрывает позиция B",
}


def cell_index(header, col):
    """Номер колонки по имени или короткому имени: 'Чем подтверждено' → 'Чем подтверждено и куда движется'."""
    key = (col or "").strip().rstrip(".").lower()
    key = ALIASES.get(key, col.strip())
    for j, c in enumerate(header):
        if c.strip() == key:
            return j
    for j, c in enumerate(header):
        if key and key.lower() in c.strip().lower():
            return j
    return None


def insert_text(cell, text, anchor):
    if not anchor:
        base = cell.rstrip()
        if base.endswith((".", "!", "?", "»")):
            return base + " " + text.strip()
        return base + ". " + text.strip()
    k = cell.find(anchor)
    if k < 0:
        return None
    # вставляем после предложения, в котором стоит якорь
    tail = cell[k:]
    dot = re.search(r"[.!?»]\s", tail)
    if dot:
        end = k + dot.end()
        return cell[:end].rstrip() + " " + text.strip() + " " + cell[end:].lstrip()
    return cell[:k + len(anchor)].rstrip() + " " + text.strip() + " " + cell[k + len(anchor):].lstrip()


def add_source(text, source_key, problems):
    """Объявить источник в шапке карты, если его там нет."""
    m = re.search(r"(?m)^sources: \[(.*?)\]$", text)
    if not m or not source_key:
        return text
    if source_key in m.group(1):
        return text
    new = m.group(1).rstrip()
    new = (new + ", " + source_key) if new else source_key
    problems.append("в шапку добавлен источник %s" % source_key)
    return text[:m.start(1)] + new + text[m.end(1):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-align", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    page = _domain.register_path(root, "conflicts")
    if not page:
        print("экземпляр не объявил карту конфликтов — править нечего")
        return 0
    text = read(page)
    rows, header = load_table(text)
    by_num = {num: (ln, cells) for ln, num, cells in rows}
    applied, skipped = [], []
    changed = False
    for pat in a.reports:
        for path in sorted(glob.glob(pat)) or [pat]:
            if not os.path.exists(path):
                skipped.append((path, "отчёта нет"))
                continue
            data = json.load(io.open(path, encoding="utf-8"))
            for it in data.get("items") or []:
                num = str(it.get("row", "")).strip().strip("*")
                col = str(it.get("col", "")).strip()
                txt = str(it.get("text", "")).strip()
                if num not in by_num:
                    skipped.append((it.get("finding"), "строки %r нет в карте" % num))
                    continue
                j = cell_index(header, col)
                if j is None:
                    skipped.append((it.get("finding"), "колонки %r нет в шапке" % col))
                    continue
                bad = reject_text(txt)
                if bad:
                    skipped.append((it.get("finding"), "текст не принят: " + "; ".join(bad)))
                    continue
                ln, cells = by_num[num]
                new_cell = insert_text(cells[j], txt, (it.get("anchor") or "").strip())
                if new_cell is None:
                    skipped.append((it.get("finding"), "якорь %r не найден в ячейке" % it.get("anchor")))
                    continue
                cells = list(cells)
                cells[j] = new_cell
                by_num[num] = (ln, cells)
                notes = []
                text = add_source(text, it.get("source_key"), notes)
                applied.append((it.get("finding"), num, col, len(txt), it.get("adds"), notes))
                changed = True
    # перестроить таблицу
    lines = text.split("\n")
    width = max(len(c) for _, c in by_num.values())
    for num, (ln, cells) in by_num.items():
        if len(cells) != width:
            skipped.append((num, "число ячеек %d, а в шапке %d" % (len(cells), width)))
            continue
        pad = [cells[0].strip()] + [c.strip() for c in cells[1:]]
        lines[ln] = "| " + " | ".join(pad) + " |"
    text = "\n".join(lines)
    for what in applied:
        print("применено: находка %s → строка %s · %s (%d знаков, %s) %s"
              % (what[0], what[1], what[2], what[3], what[4], "; ".join(what[5])))
    for who, why in skipped:
        print("пропущено: %s — %s" % (who, why))
    print("\nдополнений применено: %d; пропущено: %d" % (len(applied), len(skipped)))
    if a.write and changed:
        write(page, text)
        print("карта записана: %s" % os.path.relpath(page, root).replace("\\", "/"))
        if not a.no_align:
            r = subprocess.run([sys.executable, toolkit.script("align_tables.py"),
                                "--wiki", root, "--write"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", check=True)
            print((r.stdout or r.stderr or "").strip().split("\n")[-1])
    elif not changed:
        print("(нечего записывать)")
    return 0



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    sys.exit(main())
