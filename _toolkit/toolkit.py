#!/usr/bin/env python3
"""Раскладка механизма: где лежат инструменты и где рабочая область экземпляра.

Зачем отдельный модуль. Механизм и экземпляр живут в разных местах: инструменты — в своей папке
`_toolkit/`, а рабочая область — в проекте владельца (`_staging/`: проходы, выемки, реестры, снимки).
Пока оба лежали в одной папке, разницы не было видно, и половина скриптов искала свои же файлы через корень
проекта. Теперь такая строка ищет чужой файл в чужом месте — и молча не находит.

Правило одно на всех (решение 2026-09-22):

    свой файл   — рядом с собой:      toolkit.script("canary_test.py")
    чужая область — в корне проекта:  toolkit.area(root, "audit", "offsite-log.tsv")

Как это проверяется. §90 линтера считает строки, где инструмент ищет свой `.py` через корень проекта, и
называет файлы: число должно падать до нуля, а не объясняться.
"""
import os

TOOLKIT = os.path.dirname(os.path.abspath(__file__))
AREA = "_staging"
# Папка инструментов экземпляра внутри его рабочей области. Инструменты, написанные под материал и слова
# этого экземпляра, живут здесь — механизм их не поставляет и в своей папке не держит.
LOCAL = "local"


def script(name, root_path=None):
    """Файл механизма рядом с этим модулем; с корнем — файл такой же вендоренной копии."""
    base = os.path.join(instance_root(root_path), "_toolkit") if root_path is not None else TOOLKIT
    return os.path.join(base, name)


def python(name):
    """Команда запуска своего скрипта: путь берётся от себя, а не от корня проекта."""
    import sys
    return [sys.executable, "-X", "utf8", script(name)]


def root():
    """Корень проекта владельца: папка, внутри которой лежат и область экземпляра, и папка механизма."""
    return os.path.dirname(TOOLKIT)


def instance_root(root_path=None):
    return os.path.abspath(root_path or root())


def wiki(root_path, *parts):
    return os.path.join(instance_root(root_path), "wiki", *parts)


def raw(root_path, *parts):
    return os.path.join(instance_root(root_path), "raw", *parts)


def area(root_path, *parts):
    """Рабочая область экземпляра: `_staging/...` под корнем проекта владельца."""
    return os.path.join(instance_root(root_path), AREA, *parts)


def latest_report(directory, prefix, suffix=".json"):
    """Самый свежий отчёт с префиксом: имена несут дату-время-закладку и сортируются.

    Зачем: сводки больше не перезаписываются одним именем на день — каждая привязана к закладке
    (решение владельца 2026-09-26), а потребители берут последнюю, а не «за сегодня». Пусто — "".
    Ключ разбирается, а не сравнивается побайтово: иначе `lint-summary-2026-09-26.json` (точка, 0x2E)
    выходит «свежее» `lint-summary-2026-09-26-0714-<rev>.json` (дефис, 0x2D) — поймано проверкой.
    """
    import re as _re

    def _key(name):
        m = _re.match(r"^.+?-(\d{4}-\d{2}-\d{2})(?:-(\d{4}))?(?:-(.+))?\.json$", name)
        if not m:
            return ("", "", name)
        return (m.group(1), m.group(2) or "", m.group(3) or "")
    try:
        cands = sorted((f for f in os.listdir(directory)
                        if f.startswith(prefix) and f.endswith(suffix)), key=_key)
    except OSError:
        return ""
    return os.path.join(directory, cands[-1]) if cands else ""


def local(root_path, *parts):
    """Файл инструментов экземпляра: его область внутри проекта владельца, а не папка механизма."""
    from domain import local_name
    return area(root_path, local_name(root_path), *parts)


def is_local_path(path, root_path=None):
    """Инструмент экземпляра или файл механизма: по расположению, а не по имени."""
    name = LOCAL
    if root_path is not None:
        from domain import local_name
        name = local_name(root_path)
    parts = os.path.abspath(path).replace("\\", "/").split("/")
    return name in parts


def is_toolkit_path(path):
    """Файл механизма или файл экземпляра: по расположению, а не по имени."""
    return os.path.abspath(path).startswith(TOOLKIT)
