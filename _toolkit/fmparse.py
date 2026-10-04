"""Разбор шапки страницы — одно место на весь проект.

Владелец правит страницы прямо в Obsidian, а Obsidian переписывает списки в блочную форму:

    tags:
      - multi-agent
      - orchestration

Такую шапку читали не все инструменты: парсеры разбирали только строки «ключ: значение» и пропускали
строки «- элемент», поэтому страница объявлялась без тегов и без источников. 2026-09-18 на `a2a`
это дало ложные §4 и §20 в линтере и разошлось с производными (карта тем, метрики, проверка чисел).

`fold(d, line)` — достроить блочный элемент к последнему ключу шапки; вызывается в `else` ветке
разбора строки, когда строка не является «ключ: значение».
"""

import re


def fold(d, line):
    """Дописать блочный элемент списка («- элемент») к последнему ключу шапки d."""
    item = line.strip()
    if not item.startswith("-"):
        return False
    if not d:
        return False
    key = list(d)[-1]
    item = item[1:].strip().strip('"').strip("'")
    if not item:
        return False
    d[key] = (d[key] + ", " + item) if d[key] else item
    return True


def frontmatter(text):
    """Шапка страницы: словарь «ключ → значение». Списки отдаются строкой через запятую (обе формы)."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not m:
        return {}
    d = {}
    key = None
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            key = k.strip()
            d[key] = v.strip()
        elif key:
            fold(d, line)
    return d


def value(text, key):
    """Значение поля шапки как есть (для `sources:` в блочной форме — строкой через запятую)."""
    return frontmatter(text).get(key, "")


def items(text, key):
    """Список из поля шапки: понимает и `[a, b]`, и блочную форму со строками «- a»."""
    v = value(text, key).strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip().strip('"').strip("'") for x in v.split(",") if x.strip()]
