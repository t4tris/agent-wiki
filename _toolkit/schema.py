#!/usr/bin/env python3
import os
import re

import domain


def path(root):
    return domain.schema_path(root)


def read(root):
    target = path(root)
    if not target or not os.path.exists(target):
        return ""
    with open(target, encoding="utf-8") as f:
        return f.read()


def section(root, title):
    match = re.search(r"(?m)^## " + re.escape(title) + r"\s*\n(.*?)(?=\n## |\Z)", read(root), re.DOTALL)
    return match.group(1) if match else ""


def taxonomy(root):
    body = section(root, "Tag Taxonomy")
    return set(re.findall(r"`([\w\-/]+)`", body))


def list_values(root, title):
    return [v.strip() for v in section(root, title).splitlines() if v.strip() and not v.lstrip().startswith(("#", "-"))]


def list_items(root, title):
    values = []
    for line in section(root, title).splitlines():
        value = re.sub(r"^(?:[-*]|\d+[.)])\s*", "", line.strip())
        if value and not value.startswith("#"):
            values.append(value)
    return values


def labeled_value(root, title, label):
    match = re.search(r"(?m)^\s*(?:[-*]\s*)?" + re.escape(label) + r"\s*:\s*(.*?)\s*$", section(root, title))
    return match.group(1).strip() if match else None


def machine_values(root, title):
    return set(re.findall(r"`([^`\n]+)`", section(root, title)))
