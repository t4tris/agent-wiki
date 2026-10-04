#!/usr/bin/env python3
"""Выравнивание таблиц Markdown пробелами — канон репозитория.

Зачем: Obsidian при просмотре страницы сам переписывает таблицу, выравнивая ячейки
пробелами и растягивая разделительную строку (проверено 2026-09-15: файл менялся,
пока владелец только читал страницы, шаг — несколько секунд между страницами).
Преобразование детерминированное и обратимое: меняются пробелы в ячейках и
разделительная строка, содержание — ни на символ. Поэтому выровненная форма
принята каноном: она совпадает с тем, что пишет Obsidian, и правок «не мной»
в рабочем дереве больше не появляется.

Выравниваются обе стороны источника — мастер в `raw/` и копия в `wiki/sources/raw/` — но уже
не ради согласованности: сверку копий с мастерами владелец отключил 2026-09-15 («один раз
скопировали внутрь вики и там оно само живёт»), так что выровненная копия — просто гигиена вида.
Хеша тела в шапке мастера больше нет (решение владельца 2026-09-17, SCHEMA «Хеш — свойство собранного
пакета»), поэтому выравнивание ничего не пересчитывает. Вызов `sync_sources.py` после записи лишь досыпает копии, которых ещё нет
(`--no-sync` отключает).

Правила выравнивания:
  * таблица — две и более подряд идущих строк, начинающихся с `|`;
  * ширина колонки — самый длинный текст в ней (после strip); строка-разделитель
    печатается дефисами той же ширины;
  * ячейка печатается как `| текст |`; лишние пробелы внутри ячейки схлопываются
    до одного, ведущие и замыкающие убираются;
  * двоеточия-маркеры выравнивания колонок (`:---`, `---:`, `:---:`) сохраняются:
    это содержание строки, а не оформление;
  * замыкающий пайп строки пишется так же, как в исходнике: markdown разрешает его
    опускать, а добавленный знак — уже не пробелы;
  * если в строке есть экранированный пайп `\\|` или строки таблицы разной ширины
    (пайп вплотную к тексту), таблица не трогается — разбор был бы неверным,
    в отчёте она называется;
  * перевод строки сохраняется таким, каким был в файле (LF или CRLF).

Границы: страницы `wiki/**`, мастера `raw/**` и зеркала `wiki/sources/raw/**`
(зеркала наполняет `sync_sources.py`, но выровнены должны быть обе стороны). Содержание не меняется: пробельная
нормализация сверяется до записи, и при расхождении файл не трогается.

Запуск:
    python3 _toolkit/align_tables.py --check --wiki .   # 1 = есть невыровненные
    python3 _toolkit/align_tables.py --write --wiki .   # выровнять (мастерам — пересчёт хеша)
"""
import argparse
import os
import re
import subprocess
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raw_write

SEP = re.compile(r"^[-: ]+$")
FM = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
SEPMARK = re.compile(r"^(:?)-+(:?)$")
SEP_LINE = re.compile(r"^\|[-:\s|]+\|$")


def table_files(root):
    """Все места, где законны таблицы: страницы вики, мастера `raw/` и их зеркала.

    Зеркала берутся наравне с мастерами: их открывают в Obsidian, и выравнивание
    на обеих сторонах — единственное, что мешает просмотру переписывать файл.
    Правило детерминированное, поэтому у одинакового текста получаются одинаковые
    байты, и мастер с зеркалом не расходятся.
    """
    out = []
    for target in (toolkit.wiki(root), toolkit.raw(root)):
        if not os.path.isdir(target):
            continue
        for base, _dirs, files in os.walk(target):
            for fn in sorted(files):
                if fn.endswith(".md"):
                    out.append(os.path.join(base, fn))
    return sorted(out)


def cells(line):
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def is_separator(cs):
    return bool(cs) and all((SEP.match(c) and "-" in c) or c == "" for c in cs) and any("-" in c for c in cs)


def separator_row(orig, markers, widths):
    """Разделительная строка шириной с колонку, с сохранением маркеров выравнивания.

    Двоеточия в разделителе задают выравнивание колонки и являются содержанием строки.
    Простой паддер их выкидывал, и сторож `same_content` останавливал запись на файлах
    манифестов (там колонки выровнены). Маркеры берутся из исходной строки, ширина —
    ширина колонки, но не меньше, чем нужно маркеру (3 дефиса плюс двоеточия).
    """
    out = []
    for i in range(len(widths)):
        c = (orig[i] if i < len(orig) else "").replace(" ", "")
        m = SEPMARK.match(c)
        left = bool(m and m.group(1))
        right = bool(m and m.group(2))
        if markers is not None:
            left, right = markers[i]
        w = max(widths[i], 3 + (1 if left else 0) + (1 if right else 0))
        out.append((":" if left else "") + "-" * (w - (1 if left else 0) - (1 if right else 0)) + (":" if right else ""))
    return out


def align_table(rows, trailing=None):
    grid = [cells(r) for r in rows]
    width_n = max(len(g) for g in grid)
    grid = [g + [""] * (width_n - len(g)) for g in grid]
    widths = [0] * width_n
    for g in grid:
        if is_separator(g):
            continue
        for i, c in enumerate(g):
            widths[i] = max(widths[i], len(c))
    markers, out = None, []
    for idx, g in enumerate(grid):
        if is_separator(g):
            print_cells = separator_row(g, markers, widths)
            if markers is None:
                markers = []
                for c in g:
                    m = SEPMARK.match((c or "").replace(" ", ""))
                    markers.append((bool(m and m.group(1)), bool(m and m.group(2))))
        else:
            print_cells = [g[i].ljust(widths[i]) for i in range(width_n)]
        line = "| " + " | ".join(print_cells)
        keep_tail = trailing is None or trailing[idx]
        if keep_tail:
            out.append(line + " |")
        else:
            out.append(line.rstrip())
    return out


def split_end(line):
    """Строка без перевода строки плюс сам перевод (может быть пустым)."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def canon(text):
    """Что считать содержанием, а что оформлением — для сторожа записи.

    Оформление (нормализуется): пробелы вообще, пробелы вокруг пайпов, длина дефисов
    в разделительной строке, наличие замыкающего `|` (markdown допускает его опускать).
    Содержание (сравнивается как есть): текст ячеек, двоеточия-маркеры выравнивания
    колонок, сами символы `|` между ячейками.
    """
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if SEP_LINE.match(s):
            line = re.sub(r"-+", "-", re.sub(r"\s+", "", line))
        elif s.startswith("|"):
            line = re.sub(r"\s*\|\s*", "|", line)
        out.append(line)
    return " ".join(" ".join(out).split())


def same_content(before, after):
    """Содержание не изменилось. Ловит ошибку в самом паддере: тронул знаки — файл не пишем."""
    return canon(before) == canon(after)


def body_of(text):
    m = FM.match(text)
    return text[m.end():] if m else text


def process(text):
    """Возвращает (новый текст, изменено, пропущенные таблицы, таблицы со сломанным строением).

    Перевод строки у каждой строки свой: смешанные файлы (LF в теле, CRLF в шапке)
    не переписываются целиком — иначе правка таблицы дала бы дифф на весь файл.
    """
    lines = [split_end(l) for l in text.splitlines(keepends=True)]
    out, i, changed, skipped, structural = [], 0, False, [], []
    while i < len(lines):
        if lines[i][0].startswith("|") and i + 1 < len(lines) and lines[i + 1][0].startswith("|"):
            j = i
            while j < len(lines) and lines[j][0].startswith("|"):
                j += 1
            block = lines[i:j]
            if any("\\|" in body for body, _ in block):
                skipped.append(i + 1)
                out += block
            else:
                trailing = [body.rstrip().endswith("|") for body, _ in block]
                fixed = align_table([body for body, _ in block], trailing)
                before_block = "".join(body + end for body, end in block)
                after_block = "".join(fixed[k] + block[k][1] for k in range(len(block)))
                if not same_content(before_block, after_block):
                    # таблица написана так, что выравнивание меняло бы её строение
                    # (пайп вплотную к тексту, разное число ячеек в строках): оставляю как есть
                    structural.append(i + 1)
                    out += block
                else:
                    if fixed != [body for body, _ in block]:
                        changed = True
                    out += [(fixed[k], block[k][1]) for k in range(len(block))]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "".join(body + end for body, end in out), changed, skipped, structural


def align_file(path):
    """Выровнять один файл. Для генераторов: их таблицы пишутся как попало, а канон — выровненные.

    Возвращает True, если файл изменён. Содержание не трогается: тот же сторож, что и в основном проходе.
    """
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    fixed, changed, _skipped, _structural = process(text)
    if not changed or not same_content(text, fixed):
        return False
    plans, issues = [], []
    raw_write.plan(path, fixed, False, "", "выравнивание таблиц", plans, issues, allow_existing=True)
    if issues:
        raise RuntimeError("; ".join(issues))
    raw_write.apply(plans)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    ap.add_argument("--no-sync", action="store_true", help="не вызывать sync_sources.py после записи")
    a = ap.parse_args()

    unaligned, skipped_all, written, masters, broken, structural_all = [], [], [], [], [], []
    plans, issues = [], []
    for path in table_files(a.wiki):
        raw = open(path, "rb").read()
        text = raw.decode("utf-8")
        fixed, changed, skipped, structural = process(text)
        rel = os.path.relpath(path, a.wiki).replace(os.sep, "/")
        if skipped:
            skipped_all.append(f"{rel}: строки {skipped} — таблица с экранированным пайпом, не трогаю")
        if structural:
            structural_all.append(f"{rel}: строки {structural} — строки таблицы разной ширины, "
                                  f"выравнивание меняло бы строение, не трогаю")
        if not changed:
            continue
        if not same_content(body_of(text), body_of(fixed)):
            broken.append(rel)
            continue
        unaligned.append(rel)
        if a.write:
            raw_write.plan(path, fixed, False, "", "выравнивание таблиц", plans, issues, allow_existing=True)

    for issue in issues:
        print(issue)
    if issues:
        return 1
    for s in broken:
        print(f"! {s}: выравнивание изменило бы содержание — файл не тронут")
    if broken:
        return 2
    if a.write and plans:
        written = [os.path.relpath(path, a.wiki).replace(os.sep, "/") for path, _old, _new, _label in plans]
        raw_write.apply(plans)
    for s in skipped_all + structural_all:
        print(f"- {s}")
    if a.check:
        for rel in unaligned:
            print(f"- {rel}: таблица не выровнена")
        print(f"ИТОГО невыровненных таблиц: {len(unaligned)} из {len(table_files(a.wiki))} файлов")
        return 1 if unaligned else 0
    print(f"выровнено файлов: {len(written)}")
    for rel in written:
        print(f"- {rel}")
    if written and not a.no_sync:
        script = toolkit.script("sync_sources.py")
        r = subprocess.run([sys.executable, script, "--wiki", a.wiki],
                           capture_output=True, text=True, encoding="utf-8", check=True)
        print("копии в хранилище:", ((r.stdout or r.stderr).strip().split("\n") or [""])[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
