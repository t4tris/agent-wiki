#!/usr/bin/env python3
"""Шкала сомнения по кандидату в страницу: 1–10 из трёх измеримых якорей.

Зачем. Решение «заводить страницу» упиралось в человека целиком: любое понятие из очереди ждало слова
владельца, и инжест вставал. Порог и последствия автосоздания объявляет локальный канон; механизм считает
сомнение и применяет объявленное правило.

Якоря (чтобы шкала была измерением, а не вкусовщиной):
1. сколько независимых источников двигают понятие (записи `raw/`);
2. держит ли понятие существующая страница или её раздел;
3. может ли корпус дать определение понятию, или он называет только имя.

Сумма баллов 0–9 переводится в шкалу владельца 1–10 (сомнение = 1 + сумма).

    python3 _toolkit/page_doubt.py --wiki .                 # печать шкалы по висящим кандидатам
    python3 _toolkit/page_doubt.py --wiki . --write-tsv     # та же таблица в _staging/page-doubt.tsv
    python3 _toolkit/page_doubt.py --wiki . --apply-auto     # авторешения относительно локального порога
"""
import argparse
import glob
import io
import os
import re
import sys

import toolkit
import schema

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slugify import slug_for
from candidates import HEADER as DECISIONS_HEADER

THRESHOLD_LABEL = "Порог сомнения кандидата"
AUTO_REASON = "автосоздание, сомнение {doubt} — порог {threshold}"


def threshold(root):
    value = schema.labeled_value(root, "Локальные пороги", THRESHOLD_LABEL)
    if value is None:
        return None
    try:
        return int(value.rstrip("."))
    except ValueError:
        return None


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def synonyms(name):
    """Имена одного понятия из строки очереди: «воркер (worker, исполнитель)» → три написания."""
    inside = re.findall(r"\(([^)]*)\)", name)
    base = re.sub(r"\([^)]*\)", "", name)
    parts = [base] + [p for chunk in inside for p in chunk.split(",")]
    out = []
    for p in parts:
        p = p.strip().strip("«»")
        if len(p) > 2:
            out.append(p)
    return out or [name.strip()]


def mentions(pat, texts):
    return sum(1 for t in texts.values() if re.search(pat, t, re.IGNORECASE))


def count_sections(pat, pages):
    n = 0
    for t in pages.values():
        for h in re.findall(r"(?m)^#{2,4} (.+)$", t):
            if re.search(pat, h, re.IGNORECASE):
                n += 1
    return n


DEFINITION = (r"это\s|называ(?:ется|ют)|означа(?:ет|ют)|представляет собой|понима(?:ется|ют) под|"
              r"определя(?:ется|ют)|we call|refers to|is a |is an ")


def score(name, pages, notes):
    """Возвращает (сомнение 1–10, разбор по якорям)."""
    alts = synonyms(name)
    pat = "|".join(re.escape(a) for a in alts)
    srcs = mentions(pat, notes)
    secs = count_sections(pat, pages)
    on_pages = mentions(pat, pages)
    a1 = 0 if srcs >= 5 else 1 if srcs >= 3 else 2 if srcs >= 2 else 3
    a2 = 3 if secs else (2 if on_pages >= 3 else 0)
    defined = 0
    for t in notes.values():
        for m in re.finditer(pat, t, re.IGNORECASE):
            window = t[max(0, m.start() - 120): m.end() + 160]
            if re.search(DEFINITION, window, re.IGNORECASE):
                defined += 1
                break
        if defined:
            break
    a3 = 0 if defined else 3
    return 1 + a1 + a2 + a3, (a1, a2, a3, srcs, secs, on_pages)


def pending(root):
    """Кандидаты очереди прозы без решения в реестре кандидатов."""
    q = toolkit.area(root, "prose-candidates.tsv")
    d = toolkit.area(root, "candidate-decisions.tsv")
    decided = set()
    if os.path.exists(d):
        for line in read(d).rstrip("\n").split("\n")[1:]:
            f = line.split("\t")
            if f and f[0].strip():
                decided.add(f[0].strip().lower())
    rows = []
    if os.path.exists(q):
        for line in read(q).rstrip("\n").split("\n")[1:]:
            f = line.split("\t")
            if len(f) >= 2 and f[0].strip() and f[0].strip().lower() not in decided:
                rows.append((f[0].strip(), f[1].strip()))
    return rows


def corpus(root):
    pages = {p: read(p) for p in glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True)
             if "/sources/" not in p.replace("\\", "/")}
    notes = {p: read(p) for p in glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True)}
    return pages, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--write-tsv", action="store_true")
    ap.add_argument("--apply-auto", action="store_true",
                    help="записать авторешения (сомнение ниже порога) в реестр кандидатов")
    args = ap.parse_args()
    root = args.wiki
    limit = threshold(root)
    if limit is None:
        print("порог сомнения не объявлен в `SCHEMA.local.md`; проверка не применима")
        return 0
    pages, notes = corpus(root)
    rows = pending(root)
    out = []
    auto = []
    for key, name in rows:
        doubt, (a1, a2, a3, srcs, secs, on_pages) = score(name, pages, notes)
        hitl = doubt >= limit
        out.append((name, str(doubt), "да" if hitl else "нет",
                    f"источников {srcs} → {a1}; разделов {secs}, страниц {on_pages} → {a2}; "
                    f"определение {'есть' if a3 == 0 else 'нет'} → {a3}"))
        if not hitl:
            auto.append((key, name, doubt))
    width = max([len(r[0]) for r in out] + [9])
    print(f"{'кандидат':<{width}} | {'сомнение':<8} | {'на решение':<10} | якоря: источники / страница / определение")
    print("-" * (width + 60))
    for row in out:
        print(f"{row[0]:<{width}} | {row[1]:>8} | {row[2]:<10} | {row[3]}")
    print(f"\nкандидатов без решения: {len(rows)} | автосоздание (сомнение < {limit}): {len(auto)} | "
          f"на решение владельца (≥ {limit}): {len(rows) - len(auto)}")
    if args.write_tsv:
        p = toolkit.area(root, "page-doubt.tsv")
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            for row in out:
                f.write("\t".join(row) + "\n")
        print(f"записано: {p}")
    if args.apply_auto:
        d = toolkit.area(root, "candidate-decisions.tsv")
        if not os.path.exists(d):
            # Свежий экземпляр: реестра решений ещё нет — заводим с шапкой из candidates.py,
            # а не падаем (найдено 2026-09-25: задача page-doubt не проходила до первой волны).
            with io.open(d, "w", encoding="utf-8", newline="") as f:
                f.write(DECISIONS_HEADER if DECISIONS_HEADER.endswith("\n") else DECISIONS_HEADER + "\n")
            print(f"реестр решений заведён: {d}")
        text = read(d)
        today = __import__("datetime").date.today().isoformat()
        add = []
        for key, name, doubt in auto:
            if re.search(r"(?m)^%s\t" % re.escape(key), text):
                continue
            slug = slug_for(name)
            add.append("\t".join([key, name, "страница", slug,
                                  AUTO_REASON.format(doubt=doubt, threshold=limit), today]))
        if add:
            with io.open(d, "w", encoding="utf-8", newline="") as f:
                f.write(text.rstrip("\n") + "\n" + "\n".join(add) + "\n")
        print(f"авторешений записано: {len(add)}")


if __name__ == "__main__":
    main()
