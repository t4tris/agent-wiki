#!/usr/bin/env python3
"""Собирает описания изображений от подагентов в один файл и проверяет контракт.

    python3 _toolkit/merge_descriptions.py --staging ./_staging/telegram/incoming

Вход: `image-batches/descriptions-*.json` (kind=media-description, контракт v1).
Выход: `media-descriptions.json` — «id → {description, visible_text, note_understandable, by}».
Плюс печать списка записей, которые остались непонятными (note_understandable=false):
именно им нужен ручной контекст владельца, и именно их надо показать пользователю.
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)          # инструмент механизма зовётся через раскладку, а не по расположению соседа
import toolkit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    a = ap.parse_args()

    # порядок по номеру файла: поздняя волна описаний перекрывает раннюю.
    # у части файлов номера нет (описания отдельного корпуса) — они идут первыми и перекрываются нумерованными.
    def _ord(p):
        m = re.search(r"(\d+)\.[a-z]+$", os.path.basename(p))
        return int(m.group(1)) if m else -1
    parts = sorted(glob.glob(os.path.join(a.staging, "image-batches*", "descriptions-*.json")), key=_ord)
    if not parts:
        sys.exit("нет файлов описаний в image-batches/descriptions-*.json")

    merged, bad_reports, unclear = {}, [], []
    for p in parts:
        chk = subprocess.run(toolkit.python("check_contract.py") + ["--wiki", toolkit.root(), p],
                             capture_output=True, text=True, check=False)
        if chk.returncode != 0:
            bad_reports.append((p, chk.stdout.strip()))
            continue
        data = json.load(open(p, encoding="utf-8"))
        for it in data["items"]:
            merged[str(it["id"])] = {"description": it["description"].strip(),
                                     "visible_text": (it.get("visible_text") or "").strip(),
                                     "note_understandable": bool(it["note_understandable"]),
                                     "by": os.path.basename(p)}

    if bad_reports:
        print("отчёты, не прошедшие контракт (описания из них не взяты):")
        for p, msg in bad_reports:
            print(" ", p)
            print("   ", msg.replace("\n", "\n    ")[:400])

    out = os.path.join(a.staging, "media-descriptions.json")
    json.dump(merged, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    unclear = {k: v for k, v in merged.items() if not v["note_understandable"]}
    print(f"\nсобрано описаний: {len(merged)} из файлов: {len(parts) - len(bad_reports)}")
    print(f"записей, которые остались непонятными без контекста: {len(unclear)}")
    for k, v in list(unclear.items())[:25]:
        print(f"  {k}: {v['description'][:110]}")
    # Список непонятных записей пишется файлом, а не только печатается: владелец 2026-09-17 показал, что
    # «надо спросить владельца» нигде не оседало — печать в консоли видна один раз и живёт в истории сессии.
    unclear_path = os.path.join(a.staging, "media-unclear.tsv")
    with io.open(unclear_path, "w", encoding="utf-8", newline="") as f:
        f.write("id\tописание (что видно)\tвидимый текст\tкто описал\n")
        for k, v in sorted(unclear.items()):
            f.write("\t".join([k, v["description"].replace("\n", " ")[:300],
                                (v["visible_text"] or "").replace("\n", " ")[:200], v["by"]]) + "\n")
    print(f"\nфайл: {out}")
    print(f"непонятные без контекста: {unclear_path} ({len(unclear)} записей)")


if __name__ == "__main__":
    main()
