#!/usr/bin/env python3
import os
import shutil

MECHANISM_PATHS = ("_toolkit",)
EXTRA_PATHS = (".github",)
ROOT_FILES = (".gitignore", "AGENTS.md", "CONTRIBUTING.md", "LICENSE.md", "README.md", "mypy.ini", "ruff.toml")
LOG_TEMPLATE = "_toolkit/templates/log.md"
DEBT_TEMPLATE = "_toolkit/templates/debt.tsv"
SUPPLY_ROOT_FILES = ROOT_FILES + ("log.md",)
SKIP_DIRS = {".git", ".obsidian", "__pycache__", ".venv", ".venv-asr", "node_modules", "models", ".firecrawl", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".env", ".env.local"}
SKIP_FILES = {".env"}


def norm(path):
    return path.replace("\\", "/").strip("/")


def included(rel):
    rel = norm(rel)
    parts = MECHANISM_PATHS + EXTRA_PATHS
    return rel in SUPPLY_ROOT_FILES or any(rel == part or rel.startswith(part + "/") for part in parts)


def _copy_tree(source, dest):
    for base, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel_base = norm(os.path.relpath(base, source))
        for name in sorted(files):
            if name in SKIP_FILES or name.startswith(".env."):
                continue
            rel = norm(os.path.join(rel_base, name)) if rel_base else name
            src = os.path.join(base, name)
            dst = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)


def _copy_log_template(source, dest):
    src = os.path.join(source, *LOG_TEMPLATE.split("/"))
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dest, "log.md"))


def _copy_debt_template(source, dest):
    src = os.path.join(source, *DEBT_TEMPLATE.split("/"))
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(dest, "debt.tsv"))


def copy_worktree(source, dest):
    for name in ROOT_FILES:
        src = os.path.join(source, name)
        if not os.path.isfile(src):
            continue
        shutil.copy2(src, os.path.join(dest, name))
    for part in MECHANISM_PATHS + EXTRA_PATHS:
        src = os.path.join(source, *part.split("/"))
        if os.path.isdir(src):
            _copy_tree(src, os.path.join(dest, *part.split("/")))
    _copy_log_template(source, dest)
    _copy_debt_template(source, dest)


def prune(root):
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if name in MECHANISM_PATHS + EXTRA_PATHS:
            for base, dirs, files in os.walk(path):
                for directory in list(dirs):
                    if directory in SKIP_DIRS:
                        shutil.rmtree(os.path.join(base, directory), ignore_errors=True)
                        dirs.remove(directory)
            continue
        if name in ROOT_FILES and os.path.isfile(path):
            continue
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    _copy_log_template(root, root)
    _copy_debt_template(root, root)

