#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дашборд визуального контроля проекта: одна страница, две вкладки.

Зачем. Артефактов контроля стало много и они разного рода: карта волны инжеста, лист решений по замыслам,
выгрузка решений владельца, карта структуры проекта. Смотреть их по отдельности — искать файл и помнить,
что где. Дашборд собирает их в один файл с двумя вкладками: **контентная** (что в вики и как решено) и
**мета** (как устроен проект). Сверху — показатели, посчитанные на момент сборки: они не хранятся в тексте,
а берутся из артефактов (карточки, очереди, реестр долга, отчёт канареек, экзамен, коммиты без пуша).

Встроенные страницы живут в `<iframe srcdoc>` — то есть дашборд самодостаточен: он не читает соседние
файлы при открытии и переживает перенос. Интерактив листа решений (галочки, выгрузка JSON) внутри
дашборда работает; если браузер блокирует localStorage для `file://`, метки просто не сохранятся между
перезагрузками — сам лист и выгрузка при этом работают.

Запуск:
    python3 _toolkit/dashboard.py --wiki .            # показать, что попадёт в дашборд
    python3 _toolkit/dashboard.py --wiki . --write    # записать _staging/dashboard.html
"""
import argparse
import glob
import io
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candidates as cand
import intents as intents_mod

PAGES_DIRS = ("entities", "concepts", "comparisons", "queries")


def read(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def count(pattern, root):
    return len(glob.glob(os.path.join(root, pattern), recursive=True))


def lint_summary(root):
    """Запускает линтер и возвращает (всего находок, список ненулевых разделов)."""
    try:
        proc = subprocess.run([sys.executable, toolkit.script("lint_wiki.py"),
                               "--wiki", root], capture_output=True, text=True, encoding="utf-8", check=False)
    except OSError as ex:
        return None, [f"линтер не запустился: {str(ex)[:80]}"]
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip() or f"код {proc.returncode}"
        return None, [f"линтер завершился с ошибкой: {detail[:80]}"]
    out = proc.stdout
    total = re.search(r"ИТОГО проблем:\s*(\d+)", out)
    bad = [(m.group(1), int(m.group(2))) for m in re.finditer(r"(?m)^## (.+?):\s*(\d+)\s*$", out)
           if int(m.group(2))]
    return (int(total.group(1)) if total else None), bad


def debt_open(root):
    path = os.path.join(root, "debt.tsv")
    if not os.path.exists(path):
        return None
    rows = [l.split("\t") for l in read(path).split("\n")[1:] if l.strip()]
    return sum(1 for r in rows if len(r) > 4 and r[4] == "открыт")


def canary_numbers(root):
    files = sorted(glob.glob(toolkit.area(root, "audit", "canary-report-*.md")))
    if not files:
        return None
    t = read(files[-1])
    def grab(pat):
        m = re.search(pat, t)
        return m.group(1) if m else "?"
    return {"file": os.path.basename(files[-1]),
            "sensitivity": grab(r"Чувствительность:\s*([\d/]+)"),
            "specificity": grab(r"Специфичность:\s*([\d/]+)"),
            "attested": grab(r"Аттестовано разделов:\s*(\d+)")}


def exam_state(root):
    proc = subprocess.run([sys.executable, toolkit.script("exam.py"), "fresh",
                           "--wiki", root], capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip() or f"код {proc.returncode}"
        return {"fresh": False, "last": f"ошибка: {detail[:80]}"}
    out = proc.stdout
    fresh = "УСТАРЕЛИ" not in out
    last = re.search(r"последние ответы:\s*(\S+)", out)
    return {"fresh": fresh, "last": last.group(1) if last else "—"}


def unpushed(root):
    proc = subprocess.run(["git", "rev-list", "--count", "origin/master..HEAD"], cwd=root,
                          capture_output=True, text=True, encoding="utf-8", check=False)
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out.isdigit() else "—"


PAGE_RE = re.compile(r"(?:wiki/)?(entities|concepts|comparisons|queries)/([a-z0-9][a-z0-9\-]*)")


def missing_pages(root, text):
    """Ссылки панели на страницы, которых больше нет в вики (дефект карт: панель показывает удалённое)."""
    gone = []
    for kind, slug in set(PAGE_RE.findall(text)):
        if not os.path.exists(toolkit.wiki(root, kind, slug + ".md")):
            gone.append(f"{kind}/{slug}")
    return sorted(gone)


def panel_stamp(root, path, title):
    """Полоса состояния панели: файл, когда собран, ссылки на удалённые страницы, отстал ли от вики."""
    if not os.path.exists(path):
        return f"<div class='pstamp bad'>панель «{esc(title)}» не собрана — нет файла {esc(os.path.basename(path))}</div>"
    text = read(path)
    def stamp(pth):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(pth)))
    ts = stamp(path)                       # когда панель собрана на самом деле, а не когда закоммичена
    gone = missing_pages(root, text)
    pages = glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True)
    wiki_last = stamp(max(pages, key=os.path.getmtime)) if pages else "—"
    stale = bool(pages and ts < wiki_last)
    bits = [f"<code>{esc(os.path.relpath(path, root))}</code>", f"собрана {esc(ts)}",
            f"последняя правка вики {esc(wiki_last or '—')}"]
    cls = "ok"
    if gone:
        bits.append("ссылается на удалённое: " + ", ".join(f"<code>{esc(g)}</code>" for g in gone))
        cls = "bad"
    elif stale:
        bits.append("собрана раньше последних правок вики — пересобери генератором")
        cls = "warn"
    return f"<div class='pstamp {cls}'>панель «{esc(title)}»: " + " · ".join(bits) + "</div>"


def decisions_counts(root):
    """Сводка применённых решений владельца — строка, без таблицы: таблицу дублирует лист решений."""
    # Источник — реестр решений `intent-decisions.tsv`, а не выгрузка `intents-decisions.json`:
    # выгрузку владелец отдаёт из листа решений в браузере и она живёт в загрузках, а реестр лежит в проекте
    # всегда. Прежний путь после переезда выгрузки в архив молча отдавал пустой словарь, и сводка в шапке
    # дашборда не строилась (находка приёмки структуры 2026-09-21).
    path = toolkit.area(root, "intent-decisions.tsv")
    if not os.path.exists(path):
        return {}
    counts = {}
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0 or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip():
                counts[parts[1].strip()] = counts.get(parts[1].strip(), 0) + 1
    return counts


def embedded(path, title):
    if not os.path.exists(path):
        return f"<div class='missing'>нет файла {esc(os.path.basename(path))} — собери его генератором</div>"
    return f"<iframe class='panel' title='{esc(title)}' srcdoc=\"{esc(read(path))}\"></iframe>"


def kpi(label, value, note="", cls=""):
    return (f"<div class='kpi {cls}'><div class='k'>{esc(label)}</div>"
            f"<div class='v'>{esc(value)}</div><div class='n'>{esc(note)}</div></div>")


def render(root, vault):
    pages = sum(count(f"wiki/{d}/*.md", root) for d in PAGES_DIRS)
    tg = count("raw/telegram/*.md", root)
    corpus = len([p for p in glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True)
                  if "/telegram/" not in p.replace(os.sep, "/")])
    cards = count("_staging/cards/*.md", root)
    hang_cand = len(cand.hanging(vault, root))
    all_cand = len(cand.candidates(vault, root))
    q = intents_mod.queue(root, vault)
    hang_int = sum(1 for r in q if not r["decision"])
    total, bad = lint_summary(root)
    debt = debt_open(root)
    canary = canary_numbers(root)
    exam = exam_state(root)
    push = unpushed(root)
    dec_counts = decisions_counts(root)

    bad_txt = ", ".join(f"§{n.split('.')[0]} — {c}" for n, c in bad) if bad else "нет"
    panels = [(toolkit.area(root, "intents-review.html"), "Лист решений по замыслам"),
              (toolkit.area(root, "audit", "candidates-status.html"), "Кандидаты в страницы"),
              (toolkit.area(root, "audit", "ingest-wave-map.html"), "Карта волны инжеста"),
              (toolkit.area(root, "project-map.html"), "Карта структуры проекта")]
    bad_panels = []
    for path, title in panels:
        if not os.path.exists(path):
            bad_panels.append(f"{title}: не собрана")
            continue
        gone = missing_pages(root, read(path))
        if gone:
            bad_panels.append(f"{title}: ссылается на удалённое ({len(gone)})")
    strip_panels = kpi("Панели дашборда", f"{len(panels) - len(bad_panels)} из {len(panels)} чистых",
                       "; ".join(bad_panels) if bad_panels else "все панели собраны и без ссылок на удалённое",
                       "ok" if not bad_panels else "warn")
    strip = "".join([
        kpi("Страниц в вики", pages, "сущности + концепты + сравнения + ответы"),
        kpi("Источников", f"{tg} + {corpus}", "телеграм-заметки + манифесты и статьи"),
        kpi("Карточек извлечения", cards, "рабочий слой _staging/cards"),
        kpi("Кандидаты в страницы", f"{all_cand} / висят {hang_cand}",
            "порог схемы; решение — страница или отказ", "ok" if hang_cand == 0 else "warn"),
        kpi("Замыслы владельца", f"{len(q)} / без решения {hang_int}",
            "страница, покрыто страницей или отказ", "ok" if hang_int == 0 else "warn"),
        kpi("Линтер", f"находок {total if total is not None else '—'}",
            f"ненулевые: {bad_txt}", "ok" if not bad else "warn"),
        kpi("Открытый долг", debt if debt is not None else "—", "debt.tsv"),
        kpi("Канарейки", f"чувств. {canary['sensitivity']}, спец. {canary['specificity']}" if canary else "—",
            f"аттестовано разделов {canary['attested']}" if canary else "отчёта нет",
            "ok" if canary and canary["specificity"].split("/")[0] == canary["specificity"].split("/")[-1] else "warn"),
        kpi("Экзамен", "свежий" if exam["fresh"] else "устарел", f"последние ответы {exam['last']}",
            "ok" if exam["fresh"] else "warn"),
        kpi("Коммитов без пуша", push, "отправка — только по слову владельца",
            "ok" if push == "0" else "warn"),
        strip_panels,
    ])
    dec_line = ", ".join(f"{k} — {v}" for k, v in sorted(dec_counts.items(), key=lambda kv: -kv[1])) or "нет"
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Дашборд проекта</title>
<style>
:root {{ color-scheme:dark; --bg:#0f1116; --panel:#161a22; --panel2:#1b2029; --line:#2a3040;
        --ink:#e7eaf1; --muted:#9aa4b8; --acc:#7aa7ff; --acc2:#5b8cf0; --ok:#48c07d; --warn:#e0a33a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; color:var(--ink); background:var(--bg); }}
header {{ position:sticky; top:0; z-index:9; background:var(--panel); border-bottom:1px solid var(--line); padding:12px 18px; }}
h1 {{ margin:0 0 4px; font-size:17px; }}
.sub {{ color:var(--muted); font-size:13px; }}
.tabs {{ display:flex; gap:8px; margin-top:10px; }}
.tab {{ border:1px solid var(--line); background:var(--panel2); color:var(--ink); border-radius:8px 8px 0 0;
        padding:7px 16px; cursor:pointer; font-size:14px; }}
.tab.on {{ background:var(--acc2); border-color:var(--acc2); color:#0b0e14; font-weight:600; }}
main {{ padding:14px 18px 60px; }}
.view {{ display:none; }}
.view.on {{ display:block; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:10px; margin-bottom:14px; }}
.kpi {{ background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--line); border-radius:10px; padding:9px 12px; }}
.kpi.ok {{ border-left-color:var(--ok); }}
.kpi.warn {{ border-left-color:var(--warn); }}
.k {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
.v {{ font-size:17px; font-weight:600; margin:2px 0; }}
.n {{ color:var(--muted); font-size:12px; }}
h2 {{ font-size:15px; margin:18px 0 8px; }}
h2 .hint {{ color:var(--muted); font-weight:400; font-size:13px; }}
.panel {{ width:100%; height:78vh; border:1px solid var(--line); border-radius:10px; background:var(--panel); }}
details.fold {{ border:1px solid var(--line); border-radius:10px; background:#12161d; padding:10px 12px; margin:6px 0; }}
details.fold summary {{ cursor:pointer; color:var(--muted); font-size:13px; }}
details.fold[open] summary {{ margin-bottom:10px; }}
.pstamp {{ font-size:12px; color:var(--muted); border:1px solid var(--line); border-left:3px solid var(--line);
          border-radius:8px; padding:6px 10px; margin:6px 0; }}
.pstamp.ok {{ border-left-color:var(--ok); }}
.pstamp.warn {{ border-left-color:var(--warn); }}
.pstamp.bad {{ border-left-color:#e0625f; color:#f0b3b1; }}
.missing {{ border:1px dashed var(--warn); color:#f0d9a8; background:#2a2213; padding:12px; border-radius:10px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--muted); font-size:12px; text-transform:uppercase; }}
th, td {{ border-bottom:1px solid var(--line); padding:7px 8px; vertical-align:top; }}
td.src {{ width:34%; color:var(--muted); }}
td.dec {{ width:20%; }}
</style></head>
<body>
<header>
  <h1>Дашборд проекта — визуальный контроль</h1>
  <div class="sub">Собрано автоматически {{{{дата}}}} генератором <code>_toolkit/dashboard.py</code>: показатели считаны
  из артефактов на момент сборки, вкладки встроены целиком и работают без соседних файлов.</div>
  <div class="tabs">
    <button class="tab on" data-v="content">Контент</button>
    <button class="tab" data-v="meta">Мета</button>
  </div>
</header>
<main>
  <div class="kpis">{strip}</div>

  <div class="view on" id="content">
    <h2>Лист решений по замыслам <span class="hint">живой: галочки и «Выгрузить JSON» работают здесь же;
      выгрузка владельца применена — {esc(dec_line)}</span></h2>
    {panel_stamp(root, toolkit.area(root, "intents-review.html"), "Лист решений по замыслам")}
    <details class="fold"><summary>развернуть лист решений — 29 замыслов, галочки и «Выгрузить JSON»</summary>
      {embedded(toolkit.area(root, "intents-review.html"), "Лист решений по замыслам")}
    </details>
    <h2>Кандидаты в страницы <span class="hint">кто набрал упоминания после ваших решений — чтобы пересмотреть</span></h2>
    {panel_stamp(root, toolkit.area(root, "audit", "candidates-status.html"), "Кандидаты в страницы")}
    {embedded(toolkit.area(root, "audit", "candidates-status.html"), "Кандидаты в страницы")}
    <h2>Карта волны инжеста <span class="hint">что было в волне и куда разошлось</span></h2>
    {panel_stamp(root, toolkit.area(root, "audit", "ingest-wave-map.html"), "Карта волны инжеста")}
    {embedded(toolkit.area(root, "audit", "ingest-wave-map.html"), "Карта волны инжеста")}
  </div>

  <div class="view" id="meta">
    <h2>Карта структуры проекта <span class="hint">зачем в проекте каждый файл и папка</span></h2>
    {panel_stamp(root, toolkit.area(root, "project-map.html"), "Карта структуры проекта")}
    {embedded(toolkit.area(root, "project-map.html"), "Карта структуры проекта")}
  </div>
</main>
<script>
document.querySelectorAll('.tab').forEach(function (b) {{
  b.addEventListener('click', function () {{
    document.querySelectorAll('.tab').forEach(function (x) {{ x.classList.toggle('on', x === b); }});
    document.querySelectorAll('.view').forEach(function (v) {{ v.classList.toggle('on', v.id === b.dataset.v); }});
  }});
}});
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Дашборд визуального контроля проекта.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    commit = subprocess.run(
        ["git", "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M"], cwd=root,
        capture_output=True, text=True, encoding="utf-8", check=False)
    committed_at = commit.stdout.strip() if commit.returncode == 0 else "—"
    html = render(root, vault).replace("{{дата}}", committed_at)
    print(f"дашборд: {len(html)} знаков, панелей {html.count('<iframe')}")
    for name in ("intents-review.html", "audit/candidates-status.html", "audit/ingest-wave-map.html",
                 "project-map.html"):
        p = toolkit.area(root, name)
        print(f"  {'есть' if os.path.exists(p) else 'НЕТ '} {name}")
    if not a.write:
        print("(без --write дашборд не записан)")
        return 0
    target = toolkit.area(root, "dashboard.html")
    with io.open(target, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("записано:", os.path.relpath(target, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
