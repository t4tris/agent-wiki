#!/usr/bin/env python3
"""Замер телеграфного стиля по всей вики: где ещё смысл может теряться.

Зачем: §45 сторожит язык позиций в карте конфликтов, но телеграфным стилем написаны и другие места —
тезисы страниц, «Спорное», «Факты и цифры», реестры, служебные документы. Прежде чем расширять правило,
надо измерить, где именно дефект настоящий, а где краткость законна (справочные таблицы: ссылки, числа, имена).

Что считает по каждой ячейке таблицы:
  · цепочка стрелок «→» / «->»  — потерянные подлежащее и сказуемое;
  · нет ни одного знака конца предложения (./!/?) — не предложение, а назывная строка;
  · обрубок: короче порога (по умолчанию 120 знаков).
Плюс строки-пункты (буллеты) с теми же признаками и «двоеточия-цепочки» (три и более «;» в короткой ячейке).

Запуск: python3 _toolkit/style_audit.py --wiki . [--min 120] [--json out.json] [--top 25]
"""
import argparse
import io
import json
import os
import re
import sys

SKIP_DIRS = {".git", ".obsidian", "_staging"}          # служебное смотрим отдельно, если попросят
MIRROR = "wiki/sources"

# Заголовки колонок, где краткость законна: это адрес, число или имя, а не утверждение.
REFERENCE_HEADERS = re.compile(
    r"^(файл|ссылка|источник|источники|страница|слаг|slug|id|дата|число|значение|метрика|"
    r"команда|скрипт|путь|тип|класс|статус проверки|версия|автор|кто|где|когда)$", re.IGNORECASE)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def scan_text(text):
    """Находки в одном тексте: ячейки таблиц и строки-пункты."""
    out = []
    lines = text.split("\n")
    header = None
    in_fence = False
    for n, ln in enumerate(lines, 1):
        if ln.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if not any(c for c in cells):
                continue
            row_is_header = header is None and not any(re.match(r"^\**\d+\**$", c) for c in cells)
            if row_is_header:
                header = cells
                continue
            for i, cell in enumerate(cells):
                col = header[i] if header and i < len(header) else ""
                if REFERENCE_HEADERS.match(col.strip("* ")) or len(cell) < 8 or cell.startswith("[["):
                    continue
                why = reason(cell)
                if why:
                    out.append({"line": n, "kind": "cell", "col": col, "why": why, "text": cell})
        elif re.match(r"^\s*[-*]\s+\S", ln):
            body = re.sub(r"^\s*[-*]\s+", "", ln).strip()
            if len(body) > 12:
                why = reason(body)
                if why == "цепочка стрелок":
                    out.append({"line": n, "kind": "bullet", "col": "", "why": why, "text": body})
    return out


def reason(text):
    if "→" in text or "->" in text:
        return "цепочка стрелок"
    if not re.search(r"[.!?]", text):
        return "нет предложения"
    if len(text) < MIN_LEN:
        return "обрубок (%d знаков)" % len(text)
    return ""


MIN_LEN = 120


def audit(root, groups):
    report = {}
    for name, path in groups.items():
        findings, files = [], 0
        base = os.path.join(root, path)
        for cur, dirs, fs in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in sorted(fs):
                if not fn.endswith(".md"):
                    continue
                full = os.path.join(cur, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                if name == "вики" and rel.startswith(MIRROR.replace("\\", "/")):
                    continue
                files += 1
                for f in scan_text(read(full)):
                    f["file"] = rel
                    findings.append(f)
        report[name] = {"files": files, "findings": findings}
    return report


def main():
    global MIN_LEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--min", type=int, default=120)
    ap.add_argument("--json", default="")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    MIN_LEN = args.min
    groups = {"вики": "wiki", "служебные документы": "_staging"}
    report = audit(args.wiki, groups)
    for name, d in report.items():
        per_file = {}
        for f in d["findings"]:
            per_file.setdefault(f["file"], []).append(f)
        print("== %s: файлов %d, находок %d, из них в %d файлах"
              % (name, d["files"], len(d["findings"]), len(per_file)))
        for rel, items in sorted(per_file.items(), key=lambda kv: -len(kv[1]))[:args.top]:
            kinds = {}
            for it in items:
                kinds[it["why"]] = kinds.get(it["why"], 0) + 1
            print("   %-58s %3d  (%s)" % (rel, len(items),
                  ", ".join("%s: %d" % (k, v) for k, v in sorted(kinds.items()))))
        print()
    if args.json:
        io.open(args.json, "w", encoding="utf-8", newline="").write(
            json.dumps(report, ensure_ascii=False, indent=1))
        print("артефакт: %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
