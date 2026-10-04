#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Доктор: что есть на этой машине сейчас.

Отвечает на вопрос «готово ли окружение», а не «идёт ли путь»: путь проверяет проба на чужом корпусе
(`tasks.py probe`). Доктор смотрит двоичники, движки, ключ, интерпретатор и рабочую область и печатает
таблицу «есть / нет / чем заменить».

Ядро (Python 3.11+, `git`, `PyYAML`, хранилище) — обязательное: если чего-то из него нет, доктор выходит с
ненулевым кодом, потому что путь в этом состоянии не проходит. Необязательные части (движки расшифровки,
выгрузка Telegram, ключ Firecrawl, curl, ffmpeg) — названы, но не мешают: механизм работает в урезанном виде,
и раздел «Окружение» в `CONTRIBUTING.md` говорит, в каком именно.

Запуск: `python3 _toolkit/doctor.py --wiki .` (и `python3 _toolkit/tasks.py doctor`).
Канарейка: №175 — она убирает хранилище и требует, чтобы доктор это назвал.
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys

import toolkit

CORE = "ядро"
EXTRA = "надстройка"

# (имя, род, что проверить, чем заменить)
BINARIES = [
    ("git", CORE, "история: свежесть карты, окно мостика, §36", "поставить git"),
    ("ffmpeg", EXTRA, "конвертация аудио перед расшифровкой", "winget install Gyan.FFmpeg"),
    ("ffprobe", EXTRA, "длительность аудио", "идёт вместе с ffmpeg"),
    ("firecrawl", EXTRA, "машинный забор внешних ссылок", "npm i -g firecrawl"),
    ("curl", EXTRA, "метаданные страниц по ссылке", "в Windows уже есть"),
]
MODULES = [
    ("yaml", CORE, "проверка шапки страницы (есть откат на регулярку)", "pip install PyYAML"),
    ("telethon", EXTRA, "выгрузка Telegram", "pip install telethon"),
    ("faster_whisper", EXTRA, "расшифровка звука (локально)", "uv pip install --python .venv-asr\\Scripts\\python.exe faster-whisper"),
    ("onnx_asr", EXTRA, "второй движок расшифровки (GigaAM)", "uv pip install --python .venv-asr\\Scripts\\python.exe onnx-asr"),
]


def have_binary(name):
    return shutil.which(name) or shutil.which(name + ".cmd") or shutil.which(name + ".ps1")


def asr_python(root):
    for rel in ((".venv-asr", "Scripts", "python.exe"), (".venv-asr", "bin", "python")):
        path = os.path.join(root, *rel)
        if os.path.isfile(path):
            return path
    return None


def have_module(name, python=None):
    if python:
        code = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
        try:
            return subprocess.run([python, "-c", code, name], capture_output=True, check=False).returncode == 0
        except OSError:
            return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def python_notes(root):
    """Версия интерпретатора — ядро; расхождение `python` и `python3` — предупреждение, а не отказ.

    На Windows это разные интерпретаторы, и на этом уже спотыкались на расшифровке: запускать надо `python3`.
    Но сам по себе второй интерпретатор в системе ничего не ломает — он только ловушка, о которой надо сказать.
    """
    notes = [("Python %d.%d" % (sys.version_info.major, sys.version_info.minor), CORE,
              (sys.version_info.major, sys.version_info.minor) >= (3, 11), "нужен 3.11 или новее")]
    return notes


def python_warnings():
    other = shutil.which("python")
    if other and os.path.realpath(other) != os.path.realpath(sys.executable):
        return [f"`python` в PATH — другой интерпретатор ({other}); запускать `python3`"]
    return []


def check(root):
    rows = []
    for name, kind, why, fix in BINARIES:
        rows.append((name, kind, bool(have_binary(name)), why, fix))
    asr = asr_python(root)
    rows.append((".venv-asr", EXTRA, bool(asr), "Python ASR: faster-whisper и onnx-asr",
                 "uv venv .venv-asr --python 3.14"))
    for name, kind, why, fix in MODULES:
        available = have_module(name, asr if name in {"faster_whisper", "onnx_asr"} else None)
        rows.append((name, kind, available, why, fix))
    key = bool(os.environ.get("FIRECRAWL_API_KEY"))
    rows.append(("FIRECRAWL_API_KEY", EXTRA, key, "забор ссылок машинно", "взять бесплатный тариф firecrawl"))
    vault = toolkit.wiki(root)
    rows.append(("хранилище `wiki/`", CORE, os.path.isdir(vault), "страницы и копии источников",
                 "рабочая область экземпляра"))
    domain = toolkit.area(root, "domain.local.tsv")
    rows.append(("объявление домена", EXTRA, os.path.exists(domain),
                 "таксономия и реестры экземпляра (§89)", "заполнить `_staging/domain.local.tsv`"))
    fixture = toolkit.script(os.path.join("fixture", "sources"), root)
    rows.append(("фикстура", EXTRA, os.path.isdir(fixture), "проба на чужом корпусе", "идёт с механизмом"))
    return rows, python_notes(root)


def main():
    ap = argparse.ArgumentParser(description="Что есть на этой машине сейчас")
    ap.add_argument("--wiki", default=".", help="корень проекта")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)

    rows, notes = check(root)
    print(f"{'есть':<5} {'что':<22} {'род':<10} зачем")
    for name, kind, ok, why, _fix in rows:
        print(f"{'✓' if ok else '—':<5} {name:<22} {kind:<10} {why}")
    print("")
    print("чего нет и чем заменить:")
    missing = [r for r in rows if not r[2]]
    if not missing:
        print("  — всё на месте")
    for name, kind, _ok, _why, fix in missing:
        print(f"  {name} ({kind}): {fix}")
    print("")
    for name, kind, ok, fix in notes:
        if not ok:
            print(f"интерпретатор: {name} — {fix}")
    for w in python_warnings():
        print(f"предупреждение: {w}")

    core_missing = [r[0] for r in rows if r[1] == CORE and not r[2]] + [n[0] for n in notes if not n[2]]
    if core_missing:
        print("")
        print("ЯДРО НЕ ГОТОВО: " + ", ".join(core_missing))
        print("В этом состоянии путь вики не проходит. Раздел «Окружение» в `CONTRIBUTING.md` говорит, что делать.")
        return 1
    print("")
    print("ядро готово. Чего нет из надстройки — механизм работает без этого, в урезанном виде.")
    print("Идёт ли путь на чужом корпусе, отвечает не доктор, а проба: `python3 _toolkit/tasks.py probe`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
