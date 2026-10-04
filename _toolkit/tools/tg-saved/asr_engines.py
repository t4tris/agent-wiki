#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движки расшифровки: загрузка с объявленной деградацией.

Движки в поставку не идут — их скачивает пользователь. Значит при их отсутствии инструмент обязан сказать,
чего не хватает и что поставить, а не падать трассировкой: это правило «деградация громкая и объявленная»,
и здесь оно живёт в одном месте, а не в пяти копиях `try/except` (решение владельца 2026-09-22).

Швы (см. раздел «Окружение» в `CONTRIBUTING.md`): звук — `transcribe_audio.py` и `transcribe_dual.py`,
сравнение движков — `compare_asr.py`, длинный звук — `gigaam_long.py`.
"""

import importlib
import sys

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_stdio()

INSTALL = {
    "faster_whisper": "uv pip install --python .venv-asr\\Scripts\\python.exe faster-whisper",
    "onnx_asr": "uv pip install --python .venv-asr\\Scripts\\python.exe onnx-asr",
}

WHAT = {
    "faster_whisper": "расшифровки звука локально (движок Whisper)",
    "onnx_asr": "расшифровки звука локально (движок GigaAM)",
}


def load(module_name):
    """Импортирует движок или выходит с фразой, что поставить."""
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        sys.exit(f"нет движка расшифровки: нужен для {WHAT.get(module_name, module_name)}.\n"
                 f"Поставьте: {INSTALL.get(module_name, 'pip install ' + module_name)}\n"
                 f"Причина: {e}\n"
                 f"Без движка механизм работает: страницы, приём, ссылки и проверки идут, "
                 f"расшифровок не будет. Подробнее — раздел «Окружение» в CONTRIBUTING.md.")


def whisper_model(name="large-v3", **kw):
    """Модель Whisper: имя модели приходит первым позиционным аргументом, как у самого движка."""
    return load("faster_whisper").WhisperModel(name, **kw)


def onnx_model(name="gigaam-v3-e2e-ctc", path=None, quantization="int8"):
    return load("onnx_asr").load_model(name, path=path, quantization=quantization)
