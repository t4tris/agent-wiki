#!/usr/bin/env python3
"""Быстрый сбор метаданных по ссылкам из выгрузки (режим глотания).

    python3 _toolkit/tools/tg-saved/fetch_link_meta.py --links links.json --out links-meta.json

Для каждого URL тянет заголовок и описание тем способом, который работает:
  * github.com            → gh api (описание, звёзды, язык, темы)
  * youtu.be/youtube.com  → oEmbed (название, канал)
  * t.me/<канал>/<id>     → публичная превью-страница t.me/s (текст поста)
  * arxiv.org             → API (название, аннотация)
  * остальные             → curl + разбор <title>, og:title, og:description, description

Резюмируемо: уже собранные записи не перезапрашиваются. В контексте сохраняется
фрагмент сообщения, из которого ссылка пришла — это резерв, когда сайт не отдаёт заголовок.
"""
import argparse
import html
import json
import os
import re
import subprocess
import urllib.parse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
PROXY = None   # заполняется из --proxy; нужен там, где провайдер режет прямой доступ (VPN — локальный прокси)


def curl(url, timeout=15):
    """Возвращает (тело, http-код). Код нужен, чтобы честно помечать неоткрывающиеся ссылки."""
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA]
    if PROXY:
        cmd += ["-x", PROXY]
    cmd += ["-w", "\n__HTTP__%{http_code}", url]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
    body = p.stdout or ""
    code = ""
    if "__HTTP__" in body:
        body, code = body.rsplit("\n__HTTP__", 1)
    return body, code.strip()


def meta_from_html(page):
    def find(patterns):
        for pat in patterns:
            m = re.search(pat, page, re.IGNORECASE | re.DOTALL)
            if m:
                return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
        return ""
    title = find([r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                  r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
                  r"<title[^>]*>(.*?)</title>"])
    desc = find([r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
                 r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                 r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']+)'])
    return title, desc


def github(url):
    m = re.match(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", url)
    if not m or m.group(1) in ("topics", "orgs", "sponsors"):
        return {}
    slug = f"{m.group(1)}/{m.group(2).replace('.git','')}"
    p = subprocess.run(["gh", "api", f"repos/{slug}", "--jq",
                        "{full_name, description, stars: .stargazers_count, lang: .language, pushed: .pushed_at, topics}"],
                       capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return {"title": slug, "desc": "", "note": "не открывается: репозиторий недоступен или удалён",
                "open": False}
    d = json.loads(p.stdout)
    return {"title": d.get("full_name", slug), "desc": d.get("description") or "",
            "stars": d.get("stars"), "lang": d.get("lang"), "pushed": (d.get("pushed") or "")[:10],
            "topics": d.get("topics") or []}


def youtube(url):
    o, code = curl("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe=""))
    try:
        d = json.loads(o)
        return {"title": d.get("title", ""), "desc": d.get("author_name", ""), "note": "видео", "open": True, "status": code}
    except json.JSONDecodeError:
        return {"title": "", "desc": "", "note": f"не открывается: видео недоступно (код {code})", "open": False, "status": code}


def telegram(url):
    m = re.match(r"https?://t\.me/(?:s/)?([^/\s?]+)/(\d+)", url)
    if not m:
        return {"title": "Telegram", "desc": "ссылка на канал или приглашение", "open": False,
                "note": "не открывается: приглашение или канал без поста"}
    ch, post = m.group(1), m.group(2)
    # виджет отдаёт полный текст поста — единственный надёжный способ прочитать канал
    widget = curl(f"https://t.me/{ch}/{post}?embed=1")
    page_w = widget[0] if isinstance(widget, tuple) else widget
    wm = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page_w, re.DOTALL)
    body_w = ""
    if wm:
        body_w = html.unescape(re.sub(r"<br\s*/?>", " ", wm[0]))
        body_w = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body_w)).strip()
    page, code = curl(f"https://t.me/s/{ch}/{post}")
    title, desc = meta_from_html(page)
    body = ""
    m2 = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.DOTALL)
    if m2:
        body = html.unescape(re.sub(r"<[^>]+>", " ", m2.group(1)))
        body = re.sub(r"\s+", " ", body).strip()[:600]
    if not (title or desc or body or body_w):
        return {"title": f"@{ch}", "desc": "", "note": f"не открывается: пост @{ch}/{post} недоступен (код {code})",
                "open": False, "status": code}
    return {"title": title or f"@{ch}", "desc": desc or body, "note": f"пост @{ch}/{post}", "open": True, "status": code,
            "content": body_w[:4000]}


def arxiv(url):
    aid = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", url)
    if not aid:
        return {}
    page, code = curl(f"https://export.arxiv.org/api/query?id_list={aid.group(1)}")
    s = re.search(r"<summary>(.*?)</summary>", page, re.DOTALL)
    titles = re.findall(r"<title>(.*?)</title>", page, re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", titles[-1]).strip()) if titles else ""
    if not title:
        return {"title": "", "desc": "", "note": f"не открывается: статья arXiv недоступна (код {code})",
                "open": False, "status": code}
    return {"title": title, "desc": html.unescape(re.sub(r"\s+", " ", s.group(1)).strip())[:600] if s else "",
            "note": "статья arXiv", "open": True, "status": code}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--proxy", default=None, help="локальный прокси, если провайдер режет прямой доступ")
    ap.add_argument("--only-closed", action="store_true",
                    help="перезапросить только те ссылки, что ранее не открылись")
    a = ap.parse_args()
    global PROXY
    PROXY = a.proxy
    if PROXY:
        print(f"через прокси {PROXY}", flush=True)
    links = json.load(open(a.links, encoding="utf-8"))
    done = json.load(open(a.out, encoding="utf-8")) if os.path.exists(a.out) else {}
    if a.only_closed:
        before = len(done)
        done = {u: r for u, r in done.items() if r.get("open") is not False}
        print(f"к повторной проверке: {before - len(done)} неоткрывшихся", flush=True)
    todo = [u for u in links if u not in done]
    print(f"всего ссылок {len(links)}, уже собрано {len(done)}, к обработке {len(todo)}", flush=True)

    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def handle(url):
        host = re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc)
        try:
            if "github.com" in host:
                rec = github(url)
            elif "youtube.com" in host or "youtu.be" in host:
                rec = youtube(url)
            elif "t.me" in host:
                rec = telegram(url)
            elif "arxiv.org" in host:
                rec = arxiv(url)
            else:
                page, code = curl(url)
                t, d = meta_from_html(page)
                rec = {"title": t, "desc": d, "open": bool(t or d), "status": code,
                       "note": "сайт" if (t or d) else f"не открывается: страница не отдала заголовок (код {code})"}
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError, ValueError) as ex:
            rec = {"title": "", "desc": "", "note": f"не открывается: ошибка запроса ({str(ex)[:50]})", "open": False}
        rec["domain"] = host
        rec["ctx"] = links[url].get("ctx", "")[:300]
        rec["dates"] = links[url].get("dates", [])[:1]
        return url, rec

    counter = {"n": 0}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for url, rec in pool.map(handle, todo):
            with lock:
                done[url] = rec
                counter["n"] += 1
                if counter["n"] % 10 == 0 or counter["n"] == len(todo):
                    json.dump(done, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                    bad = sum(1 for r in done.values() if r.get("open") is False)
                    print(f"[{counter['n']}/{len(todo)}] {rec.get('domain')}: {(rec.get('title') or '—')[:60]} | не открылось всего: {bad}", flush=True)
    json.dump(done, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    empty = sum(1 for r in done.values() if not r.get("title"))
    print(f"\nготово: {len(done)} записей, без заголовка {empty} → {a.out}", flush=True)


if __name__ == "__main__":
    main()
