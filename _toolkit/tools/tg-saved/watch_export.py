#!/usr/bin/env python3
"""Ждёт завершения выгрузки Telegram, затем делает инвентаризацию, триаж и расшифровку голосовых.

    python3 _toolkit/tools/tg-saved/watch_export.py --base "<папка выгрузки Telegram Desktop>" --staging "./_staging/telegram"

Считает выгрузку завершённой, когда result.json разбирается целиком И ни один файл в папке
не менялся последние 60 секунд. После этого:
  1) import_telegram.py → messages.json, review.csv, review.md, stats.txt
  2) transcribe_audio.py → расшифровки голосовых и аудиофайлов интерпретатором `.venv-asr`
  3) сводка `watch-summary.md`
Ничего не переносит в raw/ — перенос делается только после одобрения пользователя.
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import time

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_stdio()

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ASR_PY = os.path.join(PROJECT_ROOT, ".venv-asr", "Scripts", "python.exe")
if not os.path.isfile(ASR_PY):
    ASR_PY = sys.executable


def newest_export(base):
    dirs = [d for d in glob.glob(os.path.join(base, "ChatExport_*")) if os.path.isdir(d)]
    return max(dirs, key=lambda d: os.path.getmtime(os.path.join(d, "result.json"))) if dirs else None


def newest_mtime(d):
    return max((os.path.getmtime(os.path.join(rt, f)) for rt, _, fs in os.walk(d) for f in fs), default=0)


def try_parse(rj):
    try:
        json.load(open(rj, encoding="utf-8"))
        return True, ""
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        return False, str(ex)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--staging", required=True)
    ap.add_argument("--max-wait", type=int, default=10800)
    ap.add_argument("--poll", type=int, default=30)
    ap.add_argument("--quiet-for", type=int, default=60)
    a = ap.parse_args()

    t0 = time.time()
    print(f"[{datetime.datetime.now():%H:%M:%S}] ждём завершения выгрузки в {a.base}", flush=True)
    while True:
        d = newest_export(a.base)
        if d:
            rj = os.path.join(d, "result.json")
            ok, err = try_parse(rj)
            quiet = time.time() - newest_mtime(d)
            if ok and quiet >= a.quiet_for:
                print(f"[{datetime.datetime.now():%H:%M:%S}] выгрузка закрыта: {os.path.basename(d)}", flush=True)
                break
            print(f"[{datetime.datetime.now():%H:%M:%S}] {os.path.basename(d)}: json {'ок' if ok else 'не закрыт'}, "
                  f"тишина {quiet:.0f} с", flush=True)
        if time.time() - t0 > a.max_wait:
            sys.exit("выгрузка не закрылась за отведённое время")
        time.sleep(a.poll)

    os.makedirs(a.staging, exist_ok=True)
    print("триаж...", flush=True)
    subprocess.run([PY, os.path.join(HERE, "import_telegram.py"), "--export", d, "--out", a.staging], check=True)

    data = json.load(open(os.path.join(a.staging, "messages.json"), encoding="utf-8"))
    msgs = data["messages"] if isinstance(data, dict) else data
    audio_dirs = [os.path.join(d, x) for x in ("voice_messages", "files", "round_video_messages") if os.path.isdir(os.path.join(d, x))]
    stt_dir = os.path.join(a.staging, "stt")
    audio_out = ""
    if audio_dirs:
        print("расшифровка аудио...", flush=True)
        subprocess.run([ASR_PY, os.path.join(HERE, "transcribe_audio.py"), "--audio", *audio_dirs, "--out", stt_dir], check=True)
        audio_out = stt_dir
    stt_files = sorted(glob.glob(os.path.join(stt_dir, "*.txt"))) if audio_out else []

    counts = {}
    for m in msgs:
        v = m.get("verdict", "?")
        counts[v] = counts.get(v, 0) + 1
    lines = [f"# Итог наблюдения за выгрузкой ({datetime.datetime.now():%d.%m.%Y %H:%M})", "",
             f"- папка выгрузки: `{d}`", f"- сообщений: {len(msgs)}",
 f"- чат: {data.get('chat') if isinstance(data, dict) else '—'}",
             f"- корзины триажа: " + ", ".join(f"{k} — {v}" for k, v in sorted(counts.items())),
             f"- расшифровок аудио: {len(stt_files)} → `{audio_out}`" if audio_out else "- аудио не найдено",
             "", "Дальше: пользователь отмечает `approve` в `review.csv`, затем `promote_approved.py`."]
    open(os.path.join(a.staging, "watch-summary.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    print(f"ГОТОВО: {os.path.join(a.staging, 'watch-summary.md')}", flush=True)


if __name__ == "__main__":
    main()
