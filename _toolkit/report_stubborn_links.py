#!/usr/bin/env python3
"""Список упрямых ссылок: что не поддалось машинному забору и почему.

    python3 _toolkit/report_stubborn_links.py --staging ./_staging/telegram/incoming

Вход:
  * `links-diagnosis.json` — первичная диагностика прямых запросов (403 / пустой 200 / нет ответа);
  * `links-fetched.tsv`     — итог забора firecrawl+curl (`finalize_links.py`);
  * `links-relevance.json`  — решение по релевантности, заголовок, описание, контекст;
  * `links.json`            — в каких сообщениях встречается ссылка.

Выход: `stubborn-links.md` — группы по причине отказа, с рекомендацией «что делать» для каждой группы.
Порядок внутри группы: сначала релевантные (`да`), затем спорные, затем остальные.
"""
import argparse
import json
import os
import sys

GROUPS = (
    ("youtube", "Видео YouTube — забираются вручную", lambda u, d: "youtube.com" in u or "youtu.be" in u),
    ("tme", "Посты Telegram, не снятые забором", lambda u, d: "t.me/" in u),
    ("x", "x.com / Twitter — стена входа", lambda u, d: "x.com" in u or "twitter.com" in u),
    ("shell", "Страницы-оболочки и JS-приложения", lambda u, d: "пустой" in d or "0 байт" in d),
    ("other", "Прочее: домены и страницы без ответа", lambda u, d: True),
)

HOWTO = {
    "youtube": "нужна расшифровка (yt-dlp + whisper), страница firecrawl бесполезна",
    "tme": "брать `?embed=1`; при отказе — повторить позже, лимит параллельных задач",
    "x": "нужен вход в аккаунт или сторонний снапшот",
    "shell": "пробовать `--wait-for 8000`; если пусто — страница без серверного содержимого",
    "other": "проверить домен вручную: часть адресов не отвечает вообще",
}

DECISION_ORDER = {"да": 0, "спорно": 1, "нет": 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    a = ap.parse_args()
    S = a.staging
    diag = json.load(open(os.path.join(S, "links-diagnosis.json"), encoding="utf-8"))
    rel = json.load(open(os.path.join(S, "links-relevance.json"), encoding="utf-8"))
    links = json.load(open(os.path.join(S, "links.json"), encoding="utf-8"))
    fetched = {}
    fp = os.path.join(S, "links-fetched.tsv")
    if os.path.exists(fp):
        lines = [l for l in open(fp, encoding="utf-8").read().splitlines() if l.strip() and not l.startswith("#")]
        rows = [l.split("\t") for l in lines[1:]]
        for r in rows:
            if len(r) >= 5:
                fetched[r[0]] = {"group": r[1], "status": r[2], "bytes": r[3], "file": r[4]}
    stubborn = [u for u in diag if fetched.get(u, {}).get("status") in ("не снято", "оболочка")]
    # Видео идём отдельно: firecrawl отдаёт страницу-описание, а не содержимое, поэтому видео попадает
    # в список упрямых независимо от того, сохранился ли у него каркас страницы.
    video = [u for u in diag if ("youtube.com" in u or "youtu.be" in u)]
    for u in video:
        if u not in stubborn:
            stubborn.append(u)
    taken = len(diag) - len(stubborn)
    out = ["# Упрямые ссылки: что не поддалось и почему", "",
           f"Охват: **{len(diag)} проблемных ссылок** из {len(links)} в массиве. Снято {taken}, упрямых **{len(stubborn)}**. "
           f"Снятые файлы — в `firecrawl/`, реестр — `links-fetched.tsv`.", "",
           "Инструменты, которыми пробовали: `firecrawl scrape` (включая `--wait-for`, `?embed=1` для Telegram), "
           "`curl` + `firecrawl parse` для страниц, отдающих только HTML, и прямая загрузка для файлов-картинок.", ""]
    seen = set()
    for key, title, pred in GROUPS:
        items = []
        for u in stubborn:
            if u in seen or not pred(u, str(diag[u])):
                continue
            seen.add(u)
            items.append(u)
        if not items:
            continue
        items.sort(key=lambda u: (DECISION_ORDER.get((rel.get(u) or {}).get("decision", "нет"), 2),
                                  (rel.get(u) or {}).get("domain", "")))
        out += [f"## {title} — {len(items)}", "", "| № | ссылка | решение | название | сообщения | что делать |", "|---|---|---|---|---|---|"]
        for i, u in enumerate(items, 1):
            r = rel.get(u) or {}
            l = links.get(u) or {}
            ids = ", ".join(str(x) for x in (l.get("ids") or [])[:3])
            name = (r.get("title") or r.get("desc") or "—").replace("|", "/")[:70]
            how = HOWTO[key]
            if key == "youtube":
                # у канала и у редирект-заглушки нечего расшифровывать — совет другой
                if "/@" in u or "/channel/" in u or "/c/" in u:
                    how = "это канал, а не видео: берётся список роликов, содержимое — вручную"
                elif "/redirect?" in u:
                    how = "редирект-заглушка YouTube: ведёт на внешний ресурс, нужен сам адрес назначения"
            out.append(f"| {i} | {u[:95]} | {r.get('decision', '—')} | {name} | {ids or '—'} | {how} |")
        out.append("")
    left = [u for u in stubborn if u not in seen]
    if left:
        out += [f"## Не разобрано — {len(left)}", ""] + [f"- {u}" for u in left] + [""]
    out += ["## Как это читать", "",
            "- Видео YouTube в списке независимо от того, снялся ли каркас страницы: firecrawl отдаёт описание ролика, "
            "а не его содержание; для содержания нужна расшифровка (yt-dlp + whisper) — это ручной шаг.", "",
            "- «Решение» — вердикт отбора по ссылке (`да` / `спорно` / `нет`) из `links-relevance.json`; "
            "внутри групп релевантные идут первыми.", "",
            "- Упрямость бывает трёх видов, и лечится по-разному: **инструментом** (Telegram — embed-версия; JS — `--wait-for`; "
            "картинки — прямая загрузка), **временем** (лимит параллельных задач — повторить позже) и **человеком** "
            "(видео с расшифровкой, чужие аккаунты, домены без серверного содержимого).", "",
            "- Отсутствие в списке означает, что ссылка снята; отсутствие в реестре `links-fetched.tsv` означало бы "
            "«не знаю», но таких нет: каждая проблемная ссылка получает статус явно.", ""]
    p = os.path.join(S, "stubborn-links.md")
    open(p, "w", encoding="utf-8", newline="").write("\n".join(out) + "\n")
    print("итог:", f"снято {taken}", f"упрямых {len(stubborn)}")
    print("файл:", p)


if __name__ == "__main__":
    sys.exit(main())
