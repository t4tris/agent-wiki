#!/usr/bin/env python3
"""Панели владельца не отстают от очереди. Проверка §50 линтера.

Правило: меняешь данные панели — пересобираешь панель тем же шагом. Иначе владелец открывает дашборд
и видит вчерашний день: 2026-09-16 панель кандидатов собрана в 13:54, двадцать два кандидата добавлены
в 14:34, и в дашборде их не было.

Сравнение идёт по СОДЕРЖИМОМУ входов, а не по времени правки. Первая версия сторожа смотрела mtime —
и аттестация канареек показала двадцать ложных срабатываний на ровном месте: безвредная канарейка
перезаписывает файл тем же содержимым, время меняется, содержимое нет. Хеш различает ровно то, что нужно:
данные изменились или нет.

    python3 _toolkit/panel_freshness.py --wiki .              # проверка
    python3 _toolkit/panel_freshness.py --wiki . --rebuild    # пересобрать панели и запомнить входы
    python3 _toolkit/panel_freshness.py --wiki . --record     # только запомнить входы (после ручной сборки)
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys

import toolkit

STATE = "audit/panels-state.json"
STATE_DISPLAY = "/".join((toolkit.AREA, "audit", "panels-state.json"))

# Панель → её входы. Список намеренно узкий: только те пары, которые действительно кормят панель,
# иначе сторож начнёт шуметь на любом изменении дерева.
PAIRS = [
    ("_staging/audit/candidates-status.html",
     ["_staging/candidate-decisions.tsv", "_staging/prose-candidates.tsv", "_staging/cards-registry.json"]),
    ("_staging/audit/ingest-wave-map.html", ["_staging/waves.json"]),
    ("_staging/intents-review.html", ["_staging/intent-decisions.tsv"]),
    ("_staging/dashboard.html", ["_staging/audit/candidates-status.html", "_staging/audit/ingest-wave-map.html",
                                 "_staging/intents-review.html", "_staging/project-map.html",
                                 "debt.tsv"]),
]
REBUILD = [
    ("candidates_status.py", "--wiki", ".", "--write"),
    ("ingest_wave_map.py", "--wiki", ".", "--write"),
    ("intents_review.py", "--wiki", ".", "--write"),
    ("dashboard.py", "--wiki", ".", "--write"),
]


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def digest(path):
    return hashlib.sha256(read_bytes(path)).hexdigest()[:16]


def current(root):
    """Хеши входов каждой панели на сейчас."""
    out = {}
    for panel, sources in PAIRS:
        out[panel] = {src: digest(os.path.join(root, src))
                      for src in sources if os.path.exists(os.path.join(root, src))}
    return out


def load_state(root):
    path = toolkit.area(root, STATE)
    if not os.path.exists(path):
        return {}
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def wave_open(root):
    path = toolkit.area(root, "waves.json")
    if not os.path.isfile(path):
        return False
    try:
        data = json.load(io.open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return any(wave.get("current") for wave in data.get("waves", []))


def required_panels(root):
    if wave_open(root):
        return [panel for panel, _sources in PAIRS]
    return [panel for panel, _sources in PAIRS if not panel.endswith("ingest-wave-map.html")]


def missing_panels(root):
    return [panel for panel in required_panels(root)
            if not os.path.isfile(os.path.join(root, panel)) or os.path.getsize(os.path.join(root, panel)) == 0]


def issues(root):
    state = load_state(root)
    if not state:
        return ["описи входов панелей нет: python3 _toolkit/panel_freshness.py --wiki . --record"]
    out = []
    for panel in missing_panels(root):
        out.append("панель не собрана: %s" % panel)
    for panel, now in current(root).items():
        was = state.get(panel)
        if was is None:
            continue
        for src, h in now.items():
            if was.get(src) and was[src] != h:
                out.append("%s: вход %s изменился после последней сборки — пересобери "
                           "(python3 _toolkit/panel_freshness.py --wiki . --rebuild)" % (panel, src))
                break
    return out


def record(root):
    missing = missing_panels(root)
    if missing:
        for panel in missing:
            print("панель не собрана: %s" % panel)
        return 1
    state = current(root)
    path = toolkit.area(root, STATE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("запомнено входов: %d панелей → %s" % (len(state), STATE_DISPLAY))
    return 0


def rebuild(root):
    for script, *args in REBUILD:
        if script == "ingest_wave_map.py" and not wave_open(root):
            print("  ingest_wave_map.py                         пропущен: открытой волны нет")
            continue
        cmd = [sys.executable, "-X", "utf8", toolkit.script(script), *args]
        try:
            r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        except OSError as exc:
            print(f"  {script:<46} сбой: {exc}")
            return 1
        if r.returncode != 0:
            parts = [_text(r.stdout).strip(), _text(r.stderr).strip()]
            detail = "\n".join(part for part in parts if part)
            print(f"  {script:<46} сбой: {detail or 'код возврата %d' % r.returncode}")
            return 1
        tail = (_text(r.stdout) or _text(r.stderr)).strip().splitlines()
        print(f"  {script:<46} {tail[-1][:60] if tail else ''}")
    record(root)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Панели владельца: свежесть против входов (проверка §50)")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--record", action="store_true", help="запомнить текущие входы как состояние панелей")
    ap.add_argument("--rebuild", action="store_true", help="пересобрать панели и запомнить входы")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    if a.rebuild:
        return rebuild(root)
    if a.record:
        return record(root)
    rows = issues(root)
    for r in rows:
        print(r)
    print("находок: %d" % len(rows))
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
