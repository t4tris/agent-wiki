#!/usr/bin/env python3
"""Дерево вики для семантического анализа: детерминированный обход, полный текст, волны по бюджету окна.

Зачем. Владелец 2026-09-17: «анализ должен проводиться по полностью загруженным в память модели материалам
вики (не сканер, а озарение), загрузка всего контекста в память осуществляется методом чтения всех статей
при обходе по детерминированному сгенерированному хуком дереву». Свод тезисов (`stance_context.py`) даёт
карту утверждений, но не полный текст: он был компромиссом по размеру окна. Здесь компромисс перенесён в
другую плоскость — страница читается ЦЕЛИКОМ, а на волны делится сам список страниц, не текст.

Арифметика 2026-09-17: 85 страниц вики — 1 427 289 знаков, это около 490 тысяч токенов; в окно 200k не
входит, в миллионное входит. Поэтому волны: каждая страница попадает ровно в одну волну и читается целиком;
выборочное чтение запрещено. Команда проверяет полноту и падает, если страница потеряна или задвоена.

    python3 _toolkit/tree_bundle.py --wiki .                       # дерево и раскладка волн (печать)
    python3 _toolkit/tree_bundle.py --wiki . --write               # пакеты волн в _staging/audit/bundle/
    python3 _toolkit/tree_bundle.py --wiki . --budget 200000 --write
"""
import argparse
import glob
import io
import json
import os
import sys

import toolkit

CHARS_PER_TOKEN = 2.9   # русский текст: знаков на токен, эмпирически из замеров репозитория
EXCLUDE = ("/sources/",)   # зеркало источников живёт по своим правилам и в анализ вики не входит


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def tree(root):
    """Детерминированный обход: все страницы вики по алфавиту пути, зеркала источников исключены."""
    pages = []
    for p in glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True):
        rel = os.path.relpath(p, root).replace("\\", "/")
        if any(x in "/" + rel for x in EXCLUDE):
            continue
        pages.append((rel, p))
    return sorted(pages)


def waves(pages, budget_tokens):
    """Нарезка списка страниц на волны по бюджету: страница целиком, никогда не пополам."""
    budget = int(budget_tokens * CHARS_PER_TOKEN)
    out, cur, size = [], [], 0
    for rel, path in pages:
        n = len(read(path))
        if cur and size + n > budget:
            out.append(cur)
            cur, size = [], 0
        cur.append((rel, n))
        size += n
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--budget", type=int, default=120000,
                    help="бюджет одной волны в токенах (по умолчанию 120k — окно 200k с запасом)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = args.wiki
    pages = tree(root)
    if not pages:
        sys.exit("страниц не найдено — проверьте --wiki")
    all_pages = {rel for rel, _ in pages}
    ws = waves(pages, args.budget)
    seen = [rel for w in ws for rel, _ in w]
    if len(seen) != len(pages) or set(seen) != all_pages:
        sys.exit("полнота нарушена: волны не покрывают дерево ровно по одному разу")
    total = sum(len(read(path)) for _, path in pages)
    print(f"страниц: {len(pages)} | знаков: {total} | оценка токенов: ~{int(total / CHARS_PER_TOKEN)}")
    print(f"волн при бюджете {args.budget} токенов: {len(ws)}")
    for i, w in enumerate(ws, 1):
        n = sum(x[1] for x in w)
        print(f"  волна {i}: страниц {len(w):>3} | знаков {n:>8} | ~{int(n / CHARS_PER_TOKEN):>7} токенов | "
              f"{w[0][0]} … {w[-1][0]}")
    if args.write:
        out_dir = toolkit.area(root, "audit", "bundle")
        os.makedirs(out_dir, exist_ok=True)
        manifest = {"pages": len(pages), "chars": total, "budget_tokens": args.budget, "waves": []}
        for i, w in enumerate(ws, 1):
            body = []
            for rel, _ in w:
                body.append(f"\n\n===== ФАЙЛ: {rel} =====\n")
                body.append(read(os.path.join(root, rel)))
            name = f"wave-{i}.md"
            with io.open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="") as f:
                f.write("".join(body))
            manifest["waves"].append({"file": name, "pages": [r for r, _ in w],
                                      "chars": sum(x[1] for x in w)})
        with io.open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        print(f"записано: {out_dir} (волн {len(ws)}, каждая страница целиком, {len(pages)} страниц в сумме)")


if __name__ == "__main__":
    main()
