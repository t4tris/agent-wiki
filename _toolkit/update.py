#!/usr/bin/env python3
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

import supply

MANIFEST = "manifest.json"


def revision(source):
    try:
        proc = subprocess.run(["git", "-C", source, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", check=False)
    except OSError as ex:
        print(f"не удалось прочитать ревизию источника: {ex}", file=sys.stderr)
        return "—"
    if proc.returncode != 0:
        print(f"не удалось прочитать ревизию источника: {(proc.stderr or proc.stdout).strip()}", file=sys.stderr)
        return "—"
    return proc.stdout.strip() or "—"


def update_manifest(target, source):
    path = os.path.join(target, "_toolkit", MANIFEST)
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    status = subprocess.run(["git", "-C", source, "status", "--porcelain"],
                            capture_output=True, check=False)
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    if status.returncode != 0:
        detail = (status.stderr or b"").decode("utf-8", errors="replace").strip()
        print(f"не удалось прочитать состояние источника: {detail or f'код {status.returncode}'}",
              file=sys.stderr)
    data.update({
        "mechanism_revision": revision(source),
        "mechanism_dirty": dirty,
        "source": source,
        "updated": datetime.date.today().isoformat(),
    })
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--wiki", default=".")
    args = ap.parse_args()
    source = os.path.abspath(args.source)
    target = os.path.abspath(args.wiki)
    if source == target or not os.path.isdir(os.path.join(source, "_toolkit")):
        raise SystemExit("нужен отдельный корень механизма с `_toolkit/`")
    with tempfile.TemporaryDirectory(prefix="toolkit-update-") as temp:
        supply.copy_worktree(source, temp)
        new_toolkit = os.path.join(temp, "_toolkit")
        old_toolkit = os.path.join(target, "_toolkit")
        backup = old_toolkit + ".previous"
        if os.path.exists(backup):
            shutil.rmtree(backup)
        if os.path.exists(old_toolkit):
            os.replace(old_toolkit, backup)
        try:
            shutil.copytree(new_toolkit, old_toolkit)
            for name in supply.ROOT_FILES:
                src = os.path.join(temp, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(target, name))
            update_manifest(target, source)
        except Exception:
            if os.path.exists(old_toolkit):
                shutil.rmtree(old_toolkit, ignore_errors=True)
            if os.path.exists(backup):
                os.replace(backup, old_toolkit)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    print("обновлено:", revision(source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
