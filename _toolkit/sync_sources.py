#!/usr/bin/env python3
"""Копии источников Layer 1 в Obsidian-хранилище.

Мастер — <корень>/raw: архив того, что забрано из сети, задним числом не перезаписывается.
Копия для чтения — <корень>/wiki/sources/raw: источник копируется в хранилище ОДИН РАЗ и дальше живёт
там самостоятельно, правится как нужно (в копии, например, живёт раздел «Где использован»).
Перезаписи копии мастером и обратного переноса правок нет — решение владельца 2026-09-15: «отключить
сверку зеркал, отключить затирание мастером; один раз скопировали внутрь вики и там оно само живёт».
Поэтому линтер (§8) проверяет только наличие копии, а содержание копии сторожит §42.

    python3 _toolkit/sync_sources.py
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys

import toolkit

# Только медиа вне сверки. Папка `telegram` раньше пропускалась, и зеркала 130 голосовых
# и текстовых источников жили вне синхронизации: расхождение с мастером (в том числе устаревший
# правка шапки) не видел никто — ни этот скрипт, ни линтер (§8). Канон один для всех
# источников: зеркало — копия мастера.
# `voice_messages` — звук: в хранилище ему нечего делать, там живёт текст расшифровки (см. .gitignore).
SKIP_DIRS = {"voice_messages"}   # `assets` снят: каталог убран, медиа живут в raw/<корпус>/images/


def is_readonly(path):
    try:
        state = os.stat(path)
    except OSError:
        return False
    if getattr(state, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
        return True
    return not (state.st_mode & stat.S_IWRITE)


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki", default=".")
    args = parser.parse_args()
    master = toolkit.raw(args.wiki)
    mirror = toolkit.wiki(args.wiki, "sources", "raw")
    if not os.path.isdir(master):
        sys.exit(f"нет каталога {master}")

    new, changed, same = [], [], 0
    locked = []
    for base, dirs, files in os.walk(master):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for filename in files:
            if not filename.endswith(".md"):
                continue
            source = os.path.join(base, filename)
            rel = os.path.relpath(source, master)
            destination = os.path.join(mirror, rel)
            if not os.path.exists(destination) or os.path.getsize(destination) == 0:
                new.append(rel)
            elif digest(source) != digest(destination):
                changed.append(rel)
            else:
                same += 1
            if os.path.exists(destination) and is_readonly(destination):
                locked.append(rel)

    # Копии в хранилище должны быть ЗАПИСЫВАЕМЫМИ: Obsidian сохраняет через перезапись и падает
    # с EPERM на файле только для чтения, показывая пользователю «Failed to save file».
    unlocked = 0
    for rel in locked:
        path = os.path.join(mirror, rel)
        try:
            os.chmod(path, 0o666)
            if os.name == "nt":
                subprocess.run(["attrib", "-r", path], capture_output=True, check=True)
            unlocked += 1
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"  ! не удалось снять защиту с {rel}: {error}")
    if unlocked:
        print(f"снял защиту «только для чтения»: {unlocked}")

    for rel in new:
        os.makedirs(os.path.dirname(os.path.join(mirror, rel)), exist_ok=True)
        shutil.copy2(os.path.join(master, rel), os.path.join(mirror, rel))
    print(f"без изменений: {same} | новых копий: {len(new)} | изменено в копиях: {len(changed)}")
    for rel in new:
        print(f"  + скопирован новый источник: {rel}")
    for rel in changed:
        print(f"  ~ копия отличается от мастера: {rel}  (штатно: копия живёт самостоятельно)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
