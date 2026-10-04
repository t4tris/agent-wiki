#!/usr/bin/env python3
"""Прогон проблемных ссылок через firecrawl и обновление диагностики.

    python3 _toolkit/fetch_links_firecrawl.py --staging ./_staging/telegram/incoming [--only 403|empty|noanswer|all]

Зачем: первичная диагностика (`links-diagnosis.json`) делалась прямыми запросами и дала три группы
отказов — 403 (бот-защита), «200 и 0 байт» (страницы на JS) и «нет ответа» (локальная сеть/прокси).
Firecrawl ходит со своей стороны и снимает все три причины, поэтому повторный проход — не «ещё раз то же»,
а другой инструмент для другой причины отказа.

Что делает: для каждой ссылки вызывает `firecrawl scrape` (для t.me — embed-версию поста, иначе
приходит заглушка «Download Telegram»), сохраняет markdown в `firecrawl/<slug>.md` и пишет
`firecrawl-manifest.tsv`: url, группа отказа, статус, байт, файл. Уже сохранённые файлы не перезаписывает.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time


def find_cli():
    """npm-шим на Windows: `firecrawl` в PATH — это .cmd, который subprocess без оболочки не находит."""
    import shutil
    for name in ("firecrawl", "firecrawl.cmd", "firecrawl.ps1"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (os.path.expanduser("~/AppData/Roaming/npm/firecrawl.cmd"),
                 os.path.expanduser("~/AppData/Roaming/npm/firecrawl")):
        if os.path.exists(cand):
            return cand
    return "firecrawl"


FC = find_cli()


def slug(u):
    s = re.sub(r"^https?://", "", u)
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return (s[:70] or "link") + ".md"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    ap.add_argument("--only", default="all", help="403 | empty | noanswer | all — какая группа")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число ссылок (для проб)")
    ap.add_argument("--pause", type=float, default=0.0, help="пауза между запросами, с (при лимите параллельных задач)")
    ap.add_argument("--key-file", default=None, help="файл с FIRECRAWL_API_KEY (по умолчанию .env в корне проекта)")
    a = ap.parse_args()
    diag = json.load(open(os.path.join(a.staging, "links-diagnosis.json"), encoding="utf-8"))
    groups = {"403": [], "empty": [], "noanswer": []}
    for url, val in diag.items():
        s = str(val)
        if "403" in s:
            groups["403"].append(url)
        elif "0 байт" in s:
            groups["empty"].append(url)
        elif "нет ответа" in s:
            groups["noanswer"].append(url)
    todo = []
    for g, urls in groups.items():
        if a.only in ("all", g):
            todo += [(g, u) for u in urls]
    if a.limit:
        todo = todo[:a.limit]
    out_dir = os.path.join(a.staging, "firecrawl")
    os.makedirs(out_dir, exist_ok=True)
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        # Ключ берётся у того, кто запускает: из переменной окружения или из локального `.env`
        # в корне проекта (он в .gitignore). Зашитых путей чужой машины здесь нет.
        env_p = a.key_file or os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_p):
            m = re.search(r"(?m)^FIRECRAWL_API_KEY=(.+)$", open(env_p, encoding="utf-8").read())
            if m:
                key = m.group(1).strip().strip('"')
    rows, done = [], 0
    started = time.time()
    print(f"ссылок к прогону: {len(todo)} (группы: " + ", ".join(f"{g} {len(v)}" for g, v in groups.items()) + ")")
    for g, url in todo:
        target = os.path.join(out_dir, slug(url))
        if os.path.exists(target) and os.path.getsize(target) > 400:
            rows.append((url, g, "уже было", os.path.getsize(target), os.path.basename(target)))
            continue
        fetch = url + ("?embed=1" if "t.me/" in url else "")
        cmd = [FC, "scrape", "-f", "markdown"]      # на Windows CLI — это .cmd-шим, поэтому полный путь
        if "t.me/" not in url:
            cmd.append("--only-main-content")
        cmd.append(fetch)
        if key:
            cmd += ["-k", key]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
            if p.returncode != 0:
                detail = (p.stderr or b"").decode("utf-8", errors="replace").strip()
                print(f"Firecrawl завершился с кодом {p.returncode}: {detail or 'без сообщения'}", file=sys.stderr)
                body = ""
            else:
                body = (p.stdout or b"").decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            body = ""
        ok = len(body) > 400 and "Download Telegram" not in body[:200]
        if ok:
            open(target, "w", encoding="utf-8", newline="").write(body)
            rows.append((url, g, "снято", len(body.encode()), os.path.basename(target)))
        else:
            rows.append((url, g, "пусто/отказ", len(body.encode()), ""))
        done += 1
        if a.pause:
            time.sleep(a.pause)
        if done % 10 == 0:
            print(f"  … {done}/{len(todo)} за {int(time.time()-started)} с", flush=True)
    man = os.path.join(a.staging, "firecrawl-manifest.tsv")
    head = "url\tгруппа\tстатус\tбайт\tфайл"
    old = []
    if os.path.exists(man):
        old = [l for l in open(man, encoding="utf-8").read().splitlines()[1:] if l.strip()]
    seen = {l.split("\t")[0] for l in old}
    with open(man, "w", encoding="utf-8", newline="") as f:
        f.write(head + "\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")
        for l in old:
            if l.split("\t")[0] not in {r[0] for r in rows}:
                f.write(l + "\n")
    taken = sum(1 for r in rows if r[2] == "снято")
    print(f"итог: снято {taken} из {len(rows)}; манифест: {man}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
