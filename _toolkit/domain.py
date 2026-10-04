#!/usr/bin/env python3
"""Объявление домена экземпляра: то, что механизм знать не может и знать не должен.

Зачем. Механизм агностичен по теме и по пути: он не знает, что за корпус у владельца, как называются его реестры
и какие слова в его теме считаются доменными. Раньше эти сведения лежали в коде механизма — имена корпусов в
`check_selection.py`, заголовки навыков в `PROJECT_MARKERS`, страницы регистров в сторожах. Для постороннего это
чужие слова: пользователь, делающий вики о своей теме, не должен угадывать, что такое
чужое имя страницы или корпуса и почему оно всплывает в его проверках.

Как устроено. Экземпляр объявляет свой домен файлом `_staging/domain.local.tsv`: строки `ключ<TAB>значение`,
списки — через запятую, комментарии — с `#`. Имя с `.local.` означает, что файл ведёт владелец экземпляра: в
поставку механизма он не входит, карта его не объясняет (§29), линтер о нём не спрашивает.

Чего не хватает. Объявление не обязано быть полным. Если ключа нет, проверки, которым он нужен, отвечают
«не применимо: в объявлении домена этого нет» — одной строкой, а не находкой на каждую страницу. Молчание о
неприменимости недопустимо: пустое место не должно выглядеть как успех (правило проекта).

Ключи (подробности — в `_toolkit/domain.local.example.tsv`):

    corpora        имена корпусов владельца: что считается выгрузкой, что отбором, что отброшенным
    skill_markers  слова, по которым видно, что чужой навык говорит об этой вики
    instance_forbidden  слова и пути своего экземпляра, которых в поставке быть не должно
    topics         классы тем экземпляра: читаются проверкой утечек наравне со словами темы
    registers      реестры экземпляра: карточки, боли, конфликты, инструменты, адреса, удалённые копии
    exam           вопросы экзамена и порог
    naming         политика имён страниц
    schema         путь локального канона
    contract       путь локального контракта
    owner_markup   путь постоянных правил разметки владельца
    map_intents    путь локальных замыслов карты
    local_area     имя области инструментов экземпляра
"""
import os

import toolkit


def load(root):
    """Читает объявление домена. Нет файла — пустой словарь: механизм работает и без него."""
    out = {}
    path = toolkit.area(root, "domain.local.tsv")
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            out[parts[0].strip()] = parts[1].strip()
    return out


def get(root, key, default=None):
    return load(root).get(key, default)


def path(root, key, default=None):
    rel = get(root, key, default)
    if not rel:
        return None
    return rel if os.path.isabs(rel) else os.path.join(root, rel.replace("/", os.sep))


def schema_path(root):
    return path(root, "schema")


def contract_path(root):
    return path(root, "contract")


def owner_markup_path(root):
    return path(root, "owner_markup")


def map_intents_path(root):
    return path(root, "map_intents")


def local_name(root):
    return get(root, "local_area", "local") or "local"


def values(root, key):
    """Значение-список: через запятую, пустые куски отброшены."""
    raw = get(root, key, "") or ""
    return [v.strip() for v in raw.split(",") if v.strip()]


def corpora(root):
    """Имена корпусов: первый — основной, дальше отбор и отброшенное (см. пример)."""
    return values(root, "corpora")


def topics(root):
    """Классы тем, которыми разметчик экземпляра делит свой корпус (например, основная тема и соседняя)."""
    return values(root, "topics")


def markers(root):
    """Слова домена: если они встречаются в файлах механизма, механизм тащит чужой домен."""
    return values(root, "skill_markers")


def forbidden(root):
    """Слова и пути своего экземпляра, которых в поставке быть не должно (второй ключ запрета)."""
    return values(root, "instance_forbidden")


def registers(root):
    """Реестры экземпляра: ключ=путь. Нет ключа — реестра у этого экземпляра нет."""
    out = {}
    for item in values(root, "registers"):
        if "=" in item:
            key, val = item.split("=", 1)
            out[key.strip()] = val.strip()
    return out


def register(root, key, default=None):
    """Путь объявленного реестра. Не объявлен — None: проверка, которой он нужен, не применима, а не «нет файла»."""
    return registers(root).get(key) or default


def register_path(root, key):
    """Абсолютный путь объявленного реестра — или None, если экземпляр его не объявил."""
    rel = register(root, key)
    return os.path.join(root, rel.replace("/", os.sep)) if rel else None


def describe(root):
    """Что объявлено и чего не хватает — одной строкой для отчёта."""
    d = load(root)
    if not d:
        return "объявление домена не заполнено (`_staging/domain.local.tsv`): проверки домена не применимы"
    have = ", ".join(sorted(d))
    return f"объявление домена: {len(d)} ключ(ей) — {have}"
