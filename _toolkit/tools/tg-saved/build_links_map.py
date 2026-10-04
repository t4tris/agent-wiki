#!/usr/bin/env python3
"""Карта адресов из выгрузки Telegram: id сообщения -> адреса. Один источник для сборки записей и проверки §75.

Зачем скрипт. Записи `raw/telegram/` собираются из выгрузки, и адрес материала берётся оттуда же — но сам
адрес живёт в сущностях сообщения (`text_link`), а не в тексте: в тексте остаётся «Забираем себе — тут».
До 2026-09-18 карту адресов делали руками вне репозитория, и потеря восьми адресов при инжесте не была видна
никому. Теперь карта собирается скриптом и лежит в репозитории: `_tools/tg-saved/links-by-message.json`.

Источники (берутся оба, ничего не теряется; при конфликте объединяются):
  * партии волны — `_staging/telegram/*/ingest-batches/batch-*.json`, поле `links` в элементе `items`;
  * выгрузка Telegram Desktop — `result.json`, сущности `text_entities` вида `text_link`/`link`;
  * уже существующая карта — она дополняется, а не переписывается.

    python3 _toolkit/tools/tg-saved/build_links_map.py                       # показать, что получится
    python3 _toolkit/tools/tg-saved/build_links_map.py --write               # записать карту
    python3 _toolkit/tools/tg-saved/build_links_map.py --export <result.json> [--write]

Корень канала (`https://t.me/имя`) в карте остаётся: различать «шапка поста» и «адрес материала» — дело
читателя карты (§75 и сборщик записей исключают корни каналов сами).
"""
import argparse
import glob
import io
import json
import os
import re
import sys

TOOLKIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, TOOLKIT)
import toolkit

ROOT = toolkit.root()
MAP = os.path.join(ROOT, "_tools", "tg-saved", "links-by-message.json")
BATCH_GLOBS = (toolkit.area(ROOT, "telegram", "*", "ingest-batches", "batch-*.json"),
               toolkit.area(ROOT, "telegram", "*", "batch-*.json"))


def add(acc, mid, url):
    if not mid or not url:
        return
    urls = acc.setdefault(str(mid), set())
    urls.add(url.strip().rstrip(".,;:»"))


def from_batches(acc):
    """Партии волны: у элемента `items` есть поле `links` — адреса берутся оттуда."""
    seen = 0
    for pattern in BATCH_GLOBS:
        for path in glob.glob(pattern):
            try:
                data = json.load(io.open(path, encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as ex:
                print(f"! партия не прочитана, пропущена: {path}: {ex}", file=sys.stderr)
                continue
            for item in (data.get("items") or []):
                for url in (item.get("links") or []):
                    if isinstance(url, str) and url.startswith("http"):
                        add(acc, item.get("id"), url)
                        seen += 1
    return seen


def from_export(acc, paths):
    """Выгрузка Telegram Desktop: адреса лежат в сущностях `text_entities`, а не в тексте."""
    seen = 0
    for path in paths:
        for f in (glob.glob(path) if any(ch in path for ch in "*?[") else [path]):
            try:
                data = json.load(io.open(f, encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as ex:
                print(f"! выгрузка не прочитана, пропущена: {f}: {ex}", file=sys.stderr)
                continue
            for msg in ((data.get("messages") or []) if isinstance(data, dict) else []):
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                for ent in (msg.get("text_entities") or []):
                    if isinstance(ent, dict) and ent.get("type") in ("text_link", "link"):
                        add(acc, mid, ent.get("href") or ent.get("text"))
                        seen += 1
                for url in re.findall(r"https?://\S+", str(msg.get("text") or "")):
                    add(acc, mid, url)
                    seen += 1
    return seen


def main():
    ap = argparse.ArgumentParser(description="Собрать карту адресов из выгрузки Telegram")
    ap.add_argument("--export", action="append", default=[],
                    help="result.json выгрузки Telegram Desktop (можно несколько)")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    acc = {}
    if os.path.exists(MAP):
        try:
            for mid, urls in json.load(io.open(MAP, encoding="utf-8")).items():
                for u in (urls or []):
                    add(acc, mid, u)
        except (OSError, UnicodeError, json.JSONDecodeError) as ex:
            sys.exit(f"существующая карта не прочитана ({MAP}): {ex}")
    n_batch = from_batches(acc)
    n_export = from_export(acc, a.export)
    out = {k: sorted(v) for k, v in sorted(acc.items(), key=lambda kv: int(kv[0]))}
    print("сообщений в карте: %d | адресов: %d" % (len(out), sum(len(v) for v in out.values())))
    print("  из партий волны: %d | из выгрузки: %d | было в карте: %d"
          % (n_batch, n_export, len(acc) - (1 if n_batch or n_export else 0)))
    if not a.write:
        print("\nэто показ. Записать: повтори с --write")
        return 0
    io.open(MAP, "w", encoding="utf-8", newline="").write(
        json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print("\nкарта записана: %s" % os.path.relpath(MAP, ROOT).replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
