#!/usr/bin/env python3
"""Автопроверка цитат: сверяет числа со страниц вики с текстом их источников Layer 1.

Запуск: python3 ./_toolkit/verify_numbers.py --wiki . [--write]
Число считается подтверждённым, если его цифровая запись встречается в одном из документов,
указанных в `sources:` этой страницы. Неподтверждённые числа попадают в отчёт — их смотрит человек:
часть из них законно производные (наши подсчёты, даты, размеры вики), часть указывает на ошибку переноса.
"""
import argparse
import datetime
import os
import re
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmparse import fold

NUM = re.compile(r"(?<![\w-])(\d{1,3}(?:[ \u00a0]\d{3})+|\d+(?:[.,]\d+)?)(\s?(?:%|тыс|млн|K|k|M|×|x\b|токен|t\b|долл|руб|строк|страниц|PR|LOC|мин|ч\b|сек))?")
SKIP = re.compile(r"^(19|20)\d{2}$")


def read(p):
    return open(p, encoding="utf-8").read()


def fm(t):
    m = re.match(r"^---\r?\n(.*?)\r?\n---", t, re.DOTALL)
    d = {}
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip().strip('"')
            else:
                fold(d, line)
    return d


def digits_variants(s):
    d = re.sub(r"[ \u00a0]", "", s).replace(",", ".")
    if d.endswith(".0"):
        d = d[:-2]
    out = {d}
    if "." in d:
        out.add(d.rstrip("0").rstrip("."))
    if d.isdigit() and len(d) >= 4:
        out.add(f"{d[:-3]}[ ,\u00a0]{d[-3:]}")
        if d.endswith("000"):
            out.add(f"{d[:-3]}K"); out.add(f"{d[:-3]}k"); out.add(f"{d[:-3]} K")
    return out


def expand_shortcuts(text):
    """55K -> 55000, 1.5M -> 1500000, чтобы сравнивать «55K» на источнике с «55 000» на странице."""
    def k(m):
        return str(int(float(m.group(1).replace(",", ".")) * 1000))

    def big(m):
        return str(int(float(m.group(1).replace(",", ".")) * 1000000))

    text = re.sub(r"(\d+(?:[.,]\d+)?)\s*[Kk]\b(?!\w)", k, text)
    text = re.sub(r"(\d+(?:[.,]\d+)?)\s*(?:M\b|млн)", big, text)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root, vault = a.wiki, toolkit.wiki(a.wiki)

    cache = {}

    def text_of(rel):
        p = os.path.join(root, rel.replace("/", os.sep))
        if p not in cache:
            cache[p] = read(p) if os.path.exists(p) else ""
        return cache[p]

    report, total_ok, total_bad = [], 0, 0
    for d in ("concepts", "comparisons", "entities", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".md"):
                continue
            t = read(os.path.join(full, fn))
            m = fm(t)
            srcs = [x.strip() for x in m.get("sources", "[]").strip("[]").split(",") if x.strip()]
            corpus = "\n".join(text_of(s) for s in srcs)
            norm1 = re.sub(r"[ \u00a0]", "", corpus).replace(",", ".")
            norm2 = re.sub(r"(?<=\d)[\s,\u00a0.](?=\d)", "", corpus)  # 1 500 / 43,588 / 150.000 -> 1500 / 43588
            norm = norm1 + "\n" + norm2 + "\n" + expand_shortcuts(norm1)
            body = t.split("---", 2)[-1]
            body = re.sub(r"\d{1,2}\.\d{1,2}\.\d{2,4}", " ", body)  # даты публикации — наши метаданные, не утверждение источника
            ok, bad = 0, []
            for mm in NUM.finditer(body):
                raw, unit = mm.group(1), (mm.group(2) or "").strip()
                if SKIP.match(raw) and not unit:
                    continue
                if len(re.sub(r"\D", "", raw)) < 3 and unit not in ("%", "$"):
                    continue
                variants = digits_variants(raw)
                if any(v in norm for v in variants):
                    ok += 1
                else:
                    ctx = body[max(0, mm.start() - 60):mm.end() + 60].replace("\n", " ")
                    bad.append((raw + ((" " + unit) if unit else ""), ctx.strip()))
            total_ok += ok
            total_bad += len(bad)
            if bad:
                report.append(f"### `{d}/{fn}`")
                report.append("")
                report.append(f"подтверждено числами источника: {ok}; не найдено: {len(bad)}")
                report.append("")
                for val, ctx in bad:
                    report.append(f"- `{val}` — …{ctx}…")
                report.append("")

    head = [f"# Автопроверка цитат от {datetime.date.today().isoformat()}", "",
            f"Подтверждено в источниках: {total_ok} чисел. Требует взгляда человека: {total_bad}.",
            "Это не список ошибок: производные числа (наши подсчёты, даты, объёмы страниц) тоже попадают сюда.", ""]
    text = "\n".join(head + report)
    if a.write:
        import json as _json
        out_dir = toolkit.area(root, "audit")
        os.makedirs(out_dir, exist_ok=True)
        _json.dump({"date": datetime.date.today().isoformat(), "confirmed": total_ok, "flagged": total_bad,
                    "pages_with_issues": len([x for x in report if x.startswith("### ")])},
                   open(os.path.join(out_dir, f"number-check-{datetime.date.today().isoformat()}.json"), "w", encoding="utf-8"),
                   ensure_ascii=False, indent=1)
        out = toolkit.area(root, "audit", f"number-check-{datetime.date.today().isoformat()}.md")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "w", encoding="utf-8").write(text + "\n")
        print("отчёт:", out)
    print(f"подтверждено {total_ok}, требует взгляда {total_bad}, страниц с замечаниями {len([x for x in report if x.startswith('### ')])}")


if __name__ == "__main__":
    main()
