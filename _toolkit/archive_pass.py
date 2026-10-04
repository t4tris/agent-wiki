#!/usr/bin/env python3
"""Увоз отработавшего в архив: кандидатов называет §84, переносит эта команда.

Зачем отдельно от сторожа. Сторож обязан только называть кандидатов и не двигать файлы — так решено
2026-09-21, и это правильно: правило не должно само удалять. Но перенос до сих пор делался руками, и каждый
раз приходилось заново решать, что актуально, а что пережиток. Здесь критерий ровно один и тот же: список
берётся у §84 (`lint_wiki.check_audit_references`), а не собирается заново, — иначе проверка и перенос
разойдутся, и один из них будет врать.

Что считается отработавшим (формулировка правила — в `SCHEMA.md`, «Что держит файл в поставке»): выход
прохода, которого не называет никто вне выходов проходов. Держат его код и сторож, реестр, документ поставки
или образец имени, по которому код собирает файлы (`lint-eye-*.json`).

    python3 _toolkit/archive_pass.py --wiki .            # показать список и ничего не делать
    python3 _toolkit/archive_pass.py --wiki . --write    # увезти: снятие с отслеживания плюс перенос

Файл не удаляется: он уезжает в `_staging/archive/audit/` с сохранением подпапок, остаётся на машине
владельца и в истории git. Перенос обратим.
"""
import argparse
import os
import shutil
import subprocess
import sys

import toolkit

STAGING = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join("archive", "audit")


def candidates(root):
    """Список кандидатов берём у сторожа: один критерий на проверку и на перенос."""
    sys.path.insert(0, STAGING)
    import lint_wiki
    out = []
    for line in lint_wiki.check_audit_references(root):
        rel = line.split(":", 1)[0].strip()
        if rel and os.path.exists(os.path.join(root, rel.replace("/", os.sep))):
            out.append(rel)
    return sorted(set(out))


def move(root, rel):
    """Увоз одного файла: снять с отслеживания, перенести, сохранив подпапку."""
    src = os.path.join(root, rel.replace("/", os.sep))
    dst = toolkit.area(root, ARCHIVE, rel.split("_staging/audit/", 1)[-1].replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    subprocess.run(["git", "-C", root, "rm", "-q", "--cached", "--ignore-unmatch", rel],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    shutil.move(src, dst)
    return os.path.relpath(dst, root).replace("\\", "/")


def prune(root):
    """Пустые подпапки слоя проходов после увоза — в них уже нечего объяснять."""
    audit = toolkit.area(root, "audit")
    gone = []
    for dirpath, dirnames, filenames in os.walk(audit, topdown=False):
        if dirpath != audit and not os.listdir(dirpath):
            os.rmdir(dirpath)
            gone.append(os.path.relpath(dirpath, root).replace("\\", "/"))
    return gone


def main():
    ap = argparse.ArgumentParser(description="Увоз отработавшего в архив: список даёт §84")
    ap.add_argument("--wiki", default=".", help="корень проекта")
    ap.add_argument("--write", action="store_true", help="перенести (без ключа — только показать список)")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    cand = candidates(root)
    if not cand:
        print("отработавшего нет: §84 не называет кандидатов — слой проходов держится ссылками")
        return 0
    print(f"кандидатов у §84: {len(cand)}")
    for rel in cand:
        print(("  → " if a.write else "  · ") + rel)
    if not a.write:
        print("проба: ничего не перенесено (для переноса — ключ --write)")
        return 0
    moved = [move(root, rel) for rel in cand]
    empty = prune(root)
    print(f"перенесено в архив: {len(moved)}")
    for m in moved:
        print("  + " + m)
    if empty:
        print("пустых подпапок убрано: " + ", ".join(empty))
    print("сам перенос обратим: файлы на месте, история git их помнит")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
