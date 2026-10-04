#!/usr/bin/env python3
"""Итог по проблемным ссылкам: что снято, чем и сколько весит.

    python3 _toolkit/finalize_links.py --staging ./_staging/telegram/incoming

Читает `links-diagnosis.json` (первичная диагностика прямых запросов: 403 / пустой 200 / нет ответа),
ищет по каждой ссылке сохранённый файл в `firecrawl/` и пишет `links-fetched.tsv`:

    url, группа отказа, статус, байт, файл

Статусы: `снято` (страница как markdown), `изображение` (скачан бинарник), `оболочка` (страница без
содержимого — JS-приложение), `не снято`. Ссылка, которой нет в реестре, попадает в `не снято`
явно — «не знаю» не превращается в «нет».

Способ забора фиксируется отдельной колонкой: `firecrawl`, `firecrawl --wait-for` (страницы на JS),
`curl+parse` (страница отдаёт только HTML), `curl (файл)` (не страница, а бинарник). Без этой колонки
список упрямых ссылок не воспроизводится: непонятно, чем именно пробовали.
"""
import argparse
import json
import os
import re
import sys


def slug(u):
    s = re.sub(r"^https?://", "", u)
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return (s[:70] or "link")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    args = ap.parse_args()
    diag = json.load(open(os.path.join(args.staging, "links-diagnosis.json"), encoding="utf-8"))
    fc = os.path.join(args.staging, "firecrawl")
    files = os.listdir(fc) if os.path.isdir(fc) else []
    rows = []
    for url, val in diag.items():
        s = str(val)
        group = "403" if "403" in s else "пустой 200" if "0 байт" in s else "нет ответа"
        base = slug(url)
        dom = re.sub(r"^https?://", "", url).split("/")[0]
        dom_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", dom).strip("-")
        tail_full = re.sub(r"^https?://", "", url).rstrip("/").split("/")[-1].split("?")[0]
        cand = [f for f in files if f in (base + ".md", base + "-.md", base + ".png", base + ".jpg")]
        if not cand:
            cand = [f for f in files if f.startswith(dom_slug) and (tail_full[:18] in f or os.path.splitext(f)[1].lower() == os.path.splitext(tail_full)[1].lower())]
        if not cand:
            tail = tail_full[:24]
            cand = [f for f in files if tail and tail in f]
        status, size, name = "не снято", 0, ""
        for f in cand:
            p = os.path.join(fc, f)
            n = os.path.getsize(p)
            if n < 400:
                continue
            ext = os.path.splitext(f)[1].lower()
            status = "изображение" if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") else "снято"
            size, name = n, f
            break
        if status == "не снято" and cand:
            f = cand[0]
            n = os.path.getsize(os.path.join(fc, f))
            status, size, name = "оболочка", n, f
        rows.append((url, group, status, size, name))
    # Способ забора: то, что нельзя вывести из файла, задано списком (и это честнее «угадывания по размеру»).
    WAIT_FOR = {
        "https://www.primeintellect.ai/blog/prime-agent",
        "https://www.mercor.com/blog/introducing-the-ai-productivity-index-for-accounting/",
        "https://devpass.llmgateway.io/pricing",
        "https://www.splunk.com/en_us/blog/learn/standard-operating-procedures-sop.html",
        "https://cognition.ai/blog/frontier-code",
        "https://cohere.com/blog/north-mini-code",
        "https://research.meta.ai/blog/introducing-muse-code-and-muse-spark-1-2",
        "https://x.ai/news/grok-imagine-image-2",
    }
    CURL_PARSE = {"https://super.engineering/"}
    CURL_FILE = {"https://cdn.prod.website-files.com/68a44d4040f98a4adf2207b6/6a1f16d86247e586b929a407_image10.png"}

    def method(url, status):
        if status in ("не снято",):
            return "не снято"
        if url in CURL_PARSE:
            return "curl+parse"
        if url in CURL_FILE or status == "изображение":
            return "curl (файл)"
        return "firecrawl --wait-for" if url in WAIT_FOR else "firecrawl"

    out = os.path.join(args.staging, "links-fetched.tsv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(f"# реестр забора ссылок | сгенерирован {__import__('datetime').date.today().isoformat()} "
                f"скриптом _toolkit/finalize_links.py по links-diagnosis.json + firecrawl/\n")
        f.write("url\tгруппа\tстатус\tбайт\tфайл\tспособ\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\t" + method(r[0], r[2]) + "\n")
    by = {}
    for _, g, st, n, _f in rows:
        by.setdefault(g, {}).setdefault(st, [0, 0])
        by[g][st][0] += 1
        by[g][st][1] += n
    print("итог по группам отказа (первичная диагностика → что удалось снять):")
    for g, d in by.items():
        total = sum(v[0] for v in d.values())
        parts = ", ".join(f"{st}: {v[0]}" for st, v in sorted(d.items()))
        print(f"  {g}: {total} — {parts}")
    print("реестр:", out)
    not_taken = [r[0] for r in rows if r[2] == "не снято"]
    if not_taken:
        print("\nне снято (нужен человек или другой инструмент):")
        for u in not_taken:
            print("  -", u[:110])

if __name__ == "__main__":
    sys.exit(main())
