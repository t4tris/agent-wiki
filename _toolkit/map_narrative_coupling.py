#!/usr/bin/env python3
"""Сцепка таблицы карты конфликтов с её разделами. Проверка §47 линтера.

Проблема, которую это ловит: новая строка спора (или дополнение, меняющее смысл раздела) появляется в
таблице, а разделы «Ключевые конфликты подробнее» и «Как этим пользоваться» остаются прежними. Владелец
2026-09-16: «актуализация разделов сцеплена с протоколом нахождения расхождений? если нет, автоматизируй».

Проверка: каждая строка таблицы обязана быть названа своим номером в тексте ниже таблицы — в разборе
ключевых конфликтов или в правилах выбора. Подходят и одиночные номера («№9»), и списки («№1, №2, №23»),
и диапазоны («№24–26», «№16–21»).

    python3 _toolkit/map_narrative_coupling.py --wiki .     # находки, код возврата 1 при них
"""
import argparse
import io
import os
import re
import sys

MAP = ()            # путь карты конфликтов объявляет экземпляр: см. _declared и _cmap_path ниже


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def table_rows(text):
    """Номера строк таблицы конфликтов."""
    return [int(m.group(1)) for m in re.finditer(r"(?m)^\|\s*\**(\d+)\**\s*\|", text)]


def narrative(text):
    """Текст страницы ниже таблицы: разбор ключевых конфликтов и правила выбора."""
    ends = [m.end() for m in re.finditer(r"(?m)^\|\s*\**\d+\**\s*\|.*$", text)]
    return text[max(ends):] if ends else ""


def named(text, n):
    for m in re.finditer(r"№\s*(\d+)(?:\s*[–-]\s*(\d+))?", text):
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a <= n <= b:
            return True
    return False


def issues(root):
    path = _cmap_path(root)
    if not path:
        return []
    if not os.path.exists(path):
        # Нет карты и экземпляр её не объявлял — проверять нечего: это состояние свежего экземпляра,
        # о нём говорит §89 одной строкой (разбор инвентаризации 2026-09-22).
        if not _declared(root, "conflicts"):
            return []
        return ["карты конфликтов нет: %s" % _domain.register(root, "conflicts", "?")]
    text = read(path)
    body = narrative(text)
    if not body.strip():
        return ["в карте нет разделов ниже таблицы — сцеплять не с чем"]
    out = []
    for n in table_rows(text):
        if not named(body, n):
            out.append("строка №%d не названа в разделах «Ключевые конфликты подробнее» и «Как этим "
                       "пользоваться»: новая строка обязана попасть в разбор или в правила выбора" % n)
    return out


def main():
    ap = argparse.ArgumentParser(description="Сцепка таблицы карты с её разделами")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    found = issues(os.path.abspath(a.wiki))
    for f in found:
        print(f)
    print("находок: %d" % len(found))
    return 1 if found else 0



def _cmap_path(root):
    """Путь страницы карты конфликтов: его объявляет экземпляр (`registers` в domain.local.tsv)."""
    return _domain.register_path(root, "conflicts")


def _declared(root, key):
    """Объявлен ли этот реестр у экземпляра. Нет объявления домена — проверка не применима, а не «нет файла»:
    у постороннего своя раскладка, и требовать от него наши страницы значит требовать чужих данных."""
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import domain
    return key in domain.registers(root)


import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    sys.exit(main())
