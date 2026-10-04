#!/usr/bin/env python3
"""Повторяемая проверка внешних ссылок: гниль домены и мёртвые адреса.

Зачем скрипт: разовая сверка 2026-09-14 (`_staging/audit/lint-pass-2026-09-14.md`) была сделана
одноразовым конвейером команд, и доказательством в реестре долгов стоял просто отчёт — проверить
«числа прохода совпадают с содержимым» было нечем. Здесь тот же проход, но командой.

Два правила, оплаченных дефектами:
  * markdown-экранирование снимается ПЕРЕД проверкой: первый проход объявил 37 мёртвых ссылок
    (и «гниль домена code.claude.com»), потому что проверял `code\\.claude\\.com` как есть —
    после снятия экранирования 44 из них оказались живыми (реестр, строка `link-check-escapes`);
  * проверяется то, что отдаёт сервер: редиректы следуются (`-L`), ответ 2xx/3xx считается живым,
    4xx/5xx и таймаут — мёртвым; 403 у живого адреса попадает в отдельный список «нужен браузер».

Запуск: python3 _toolkit/check_links.py [--wiki .] [--write] [--limit N] [--jobs N]
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import toolkit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) wiki-link-check/1.0"

LINK = re.compile(r"https?://[^\s)>\]\"'`]+")


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def unescape(url):
    """`code\\.claude\\.com` → `code.claude.com`: экранирование markdown к адресу не относится."""
    return url.replace("\\", "")


def collect(root):
    """Внешние ссылки: мастер `raw/` (без зеркала) и страницы вики."""
    urls = {}
    files = sorted(glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True))
    files += sorted(glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True))
    files = [f for f in files if os.sep + "sources" + os.sep not in f]
    for f in files:
        text = re.sub(r"```.*?```", " ", read(f), flags=re.DOTALL)      # код — не адреса для проверки
        for m in LINK.finditer(text):
            url = unescape(m.group(0)).rstrip(".,;:")
            urls.setdefault(url, []).append(os.path.relpath(f, root))
    return urls


def probe(url):
    r = subprocess.run(["curl", "-sS", "-o", os.devnull, "-L", "--max-time", "20",
                        "-A", UA, "-w", "%{http_code}", url],
                       capture_output=True, text=True, errors="ignore", check=False)
    if r.returncode != 0:
        detail = (r.stderr or f"код curl {r.returncode}").strip()
        return url, "dead", detail[:80]
    code = (r.stdout or "").strip()[-3:]
    if not code or code == "000":
        return url, "timeout", (r.stderr or "").strip()[:80]
    n = int(code) if code.isdigit() else 0
    if 200 <= n < 400:
        return url, "alive", code
    if n in (401, 403, 405, 406, 426, 429):
        # 405/406/426 — сервер отбил именно curl (метод, заголовки, протокол), а не адрес умер:
        # первый проход по ссылкам уже один раз объявил живые адреса мёртвыми, повторять не будем.
        return url, "blocked", code
    return url, "dead", code


def main():
    ap = argparse.ArgumentParser(description="внешние ссылки: живые, мёртвые, заблокированные")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true", help="записать отчёт в _staging/audit/")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    urls = collect(root)
    items = sorted(urls)
    if args.limit:
        items = items[:args.limit]
    print("ссылок к проверке: " + str(len(items)) + " (из " + str(len(urls)) + " уникальных)")
    result = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for i, (url, status, detail) in enumerate(pool.map(probe, items), 1):
            result.append((url, status, detail, urls[url]))
            if i % 50 == 0:
                print("  проверено " + str(i) + "/" + str(len(items)), flush=True)
    dead = [r for r in result if r[1] == "dead"]
    blocked = [r for r in result if r[1] == "blocked"]
    alive = [r for r in result if r[1] == "alive"]
    print("\nживых: " + str(len(alive)) + " | заблокировано (нужен браузер): " + str(len(blocked)) +
          " | мёртвых: " + str(len(dead)))
    for url, _, code, files in dead[:20]:
        print("  МЁРТВА " + code + " " + url + "  ← " + (files[0] if files else "?"))
    if args.write:
        import datetime
        date = datetime.date.today().isoformat()
        ad = toolkit.area(root, "audit")
        report = ["# Проход ссылок, " + date, "",
                  "Проверено внешних ссылок: " + str(len(items)) + " в " + str(len({f for v in urls.values() for f in v})) +
                  " файлах `raw/` и `wiki/` (зеркало исключено).",
                  "Живых: " + str(len(alive)) + "; заблокированных для curl (401/403/429): " + str(len(blocked)) +
                  "; мёртвых: " + str(len(dead)) + ".", "",
                  "Метод: `python3 _toolkit/check_links.py --wiki . --write` — markdown-экранирование снимается",
                  "до проверки (урок строки `link-check-escapes`), редиректы следуются (`curl -L`), код 2xx/3xx — жив, 4xx/5xx и таймаут — мёртв.", ""]
        if dead:
            report.append("## Мёртвые")
            report += ["- `" + code + "` " + url + " — " + (files[0] if files else "?") for url, _, code, files in dead]
        if blocked:
            report.append("\n## Заблокированные (у живого адреса может стоять защита)")
            report += ["- `" + code + "` " + url for url, _, code, _ in blocked]
        with io.open(os.path.join(ad, "links-pass-" + date + ".md"), "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(report) + "\n")
        with io.open(os.path.join(ad, "links-pass-" + date + ".json"), "w", encoding="utf-8", newline="") as f:
            json.dump({"date": date, "checked": len(items), "alive": len(alive),
                       "blocked": len(blocked), "dead": len(dead),
                       "dead_list": [{"url": u, "code": c, "files": fs} for u, _, c, fs in dead]},
                      f, ensure_ascii=False, indent=1)
        print("отчёт: _staging/audit/links-pass-" + date + ".md")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
