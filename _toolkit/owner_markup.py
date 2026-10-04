#!/usr/bin/env python3
import json
import os

import domain


def load(root):
    path = domain.owner_markup_path(root)
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def required(root):
    data = load(root)
    if not data:
        raise ValueError("объявление постоянной разметки не заполнено: укажите `owner_markup` в domain.local.tsv")
    return data
