#!/usr/bin/env python3
"""Замок «один писатель»: заявка на общий файл волны.

Правило существует с 2026-09-17 и до сих пор держалось честным словом. Цена нарушений известна: при
параллельной правке восемью детьми из `wiki/index.md` молча исчезли две строки, а один из детей переписал
соседний скрипт так, что тот падал. Замок не запрещает писать — он не даёт двум работникам заявить один файл.

    python3 _toolkit/wave_lock.py --wiki . claim wiki/index.md --who child-3
    python3 _toolkit/wave_lock.py --wiki . release wiki/index.md --who child-3
    python3 _toolkit/wave_lock.py --wiki . list
    python3 _toolkit/wave_lock.py --wiki . check          # код 1, если что-то занято (ворота раннера)

Заявка живёт до снятия: замок про порядок работы, а не про время. Забытую заявку видно в `list`.
"""
import argparse
import datetime
import io
import json
import os
import sys

import toolkit

LOCK = "wave-lock.json"


def load(path):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f"замок {path} не прочитан: {ex}; операция отклонена", file=sys.stderr)
        raise SystemExit(2) from ex


def save(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def main():
    ap = argparse.ArgumentParser(description="Замок «один писатель» для общих файлов волны")
    ap.add_argument("command", choices=["claim", "release", "list", "check"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--who", default="")
    ap.add_argument("--all", action="store_true", help="release: снять все заявки")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    path = toolkit.area(root, LOCK)
    data = load(path)
    if a.command == "list":
        if not data:
            print("заявок нет")
            return 0
        for f, d in sorted(data.items()):
            print("%-52s %s (%s)" % (f, d.get("who", "?"), d.get("at", "?")))
        return 0
    if a.command == "check":
        if data:
            for f, d in sorted(data.items()):
                print("занят: %s — %s" % (f, d.get("who", "?")))
            return 1
        print("заявок нет")
        return 0
    if a.command == "release" and a.all:
        save(path, {})
        print("все заявки сняты")
        return 0
    if not a.files or not a.who:
        print("нужно: %s <файл...> --who <кто>" % a.command)
        return 2
    if a.command == "claim":
        for f in a.files:
            held = data.get(f)
            if held and held.get("who") != a.who:
                print("отказ: %s уже заявлен — %s" % (f, held.get("who", "?")))
                return 1
        for f in a.files:
            data[f] = {"who": a.who, "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        save(path, data)
        print("заявлено: " + ", ".join(a.files))
        return 0
    for f in a.files:
        if f not in data:
            print("нет заявки: %s" % f)
            continue
        if data[f].get("who") != a.who:
            print("заявку на %s держит %s — снимать ей" % (f, data[f].get("who")))
            return 1
        del data[f]
    save(path, data)
    print("снято: " + ", ".join(a.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
