#!/usr/bin/env python3
"""Добивает ссылки, которые не открылись ни напрямую, ни через VPN-прокси: тянет их Firecrawl'ом.

    python3 _toolkit/tools/tg-saved/close_links_with_firecrawl.py --meta links-meta.json --outdir fc-links

Firecrawl — внешний сервис: его запросы идут не с этого канала, поэтому он проходит там, где
curl получает 403 от Cloudflare или пустое тело. Результат: markdown-файл на каждую ссылку,
а в meta дописываются title/desc и признак открытия.
"""
import argparse
import json
import os
import re
import subprocess
import sys

UA = "Mozilla/5.0 (compatible; agent-wiki/1.0)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--key-file", default=None, help="файл с FIRECRAWL_API_KEY (по умолчанию .env в корне проекта)")
    a = ap.parse_args()

    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        # Ключ — из переменной окружения, из --key-file или из локального `.env`
        # в корне проекта (он в .gitignore). Зашитых путей чужой машины здесь нет.
        kf = a.key_file or os.path.join(os.getcwd(), ".env")
        if os.path.exists(kf):
            m = re.search(r"(?m)^FIRECRAWL_API_KEY=(.+)$", open(kf, encoding="utf-8", errors="replace").read())
            key = (m.group(1).strip().strip('"') if m else "")
    if not key:
        sys.exit("нет FIRECRAWL_API_KEY")
    env = dict(os.environ, FIRECRAWL_API_KEY=key)
    # в Windows npm ставит firecrawl.cmd: CreateProcess не ищет bat/cmd по имени без расширения
    cli = "firecrawl"
    for cand in (os.path.join(os.environ.get("APPDATA", ""), "npm", "firecrawl.cmd"),
                 os.path.join(os.environ.get("APPDATA", ""), "npm", "firecrawl"),
                 "firecrawl.cmd"):
        if os.path.exists(cand):
            cli = cand
            break
    print("CLI:", cli, flush=True)
    os.makedirs(a.outdir, exist_ok=True)

    meta = json.load(open(a.meta, encoding="utf-8"))
    closed = [u for u, r in meta.items() if r.get("open") is False]
    print(f"к обработке: {len(closed)} ссылок", flush=True)
    done, failed = 0, []
    for i, url in enumerate(closed, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://", "", url).lower())[:70].strip("-")
        out = os.path.join(a.outdir, slug + ".md")
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            subprocess.run([cli, "scrape", url, "--only-main-content", "-o", out],
                           env=env, capture_output=True, text=True, timeout=180, check=False)
        if os.path.exists(out) and os.path.getsize(out) > 400:
            body = open(out, encoding="utf-8", errors="replace").read()
            t = re.search(r"(?m)^#\s+(.+)$", body)
            p = re.search(r"(?m)^([^\n#\[\!][^\n]{140,})$", body)
            meta[url].update({"open": True, "note": "открыта через Firecrawl",
                              "title": (t.group(1).strip()[:110] if t else meta[url].get("title") or ""),
                              "desc": re.sub(r"\s+", " ", p.group(1))[:200] if p else meta[url].get("desc", ""),
                              "content": re.sub(r"\s+", " ", body)[:4000]})
            done += 1
            print(f"[{i}/{len(closed)}] ок: {url[:70]}", flush=True)
        else:
            failed.append(url)
            print(f"[{i}/{len(closed)}] мимо: {url[:70]}", flush=True)
        json.dump(meta, open(a.meta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nоткрыто через Firecrawl: {done}; остались закрытыми: {len(failed)}", flush=True)
    for u in failed:
        print("   ", u)


if __name__ == "__main__":
    main()
