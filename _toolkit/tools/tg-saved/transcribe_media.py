#!/usr/bin/env python3
"""Расшифровка одного медиафайла в Markdown для конвейера отбора.

Правило владельца: голосовое/фото без текста в корпус не идёт — идёт только с расшифровкой,
дальше запись работает как обычный md. Режимы и проверки — из скилла local-asr-to-markdown,
пути — от корня проекта, а не из чужого дерева.

    python3 _toolkit/tools/tg-saved/transcribe_media.py --media <файл> [--mode fast|dual|eng] [--wiki .]

Аудио/видео — движки `.venv-asr` (fast: GigaAM по-русски, eng: Whisper по-английски, dual: оба + fusion).
Видео предварительно разбирается в mono WAV 16 кГц через ffmpeg. Изображение — локальный Windows OCR;
если движок недоступен — отказ с указанием расшифровать зрением агента по images-playbook.
Результат — `<имя>.md` рядом с медиа, с шапкой происхождения. Существующий не перезаписывается.
"""
import argparse
import os
import re
import subprocess
import sys

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma", ".ogg", ".oga", ".opus"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def fail(message):
    print("отказ: %s" % message)
    return 2


def run(cmd, what):
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
        print("%s: %s" % (what, tail[-1][:160] if tail else "код %d" % proc.returncode))
        return None
    return proc


def asr_python(root):
    py = os.path.join(root, ".venv-asr", "Scripts", "python.exe")
    if not os.path.isfile(py):
        print("нет интерпретатора ASR-окружения: %s" % py)
        return None
    return py


def origin(mode, engine, source):
    return "<!-- origin: %s | engine: %s | source: %s -->\n\n" % (mode, engine, source)


def transcribe_audio_video(args, root, work, stem, result, ext):
    py = asr_python(root)
    if not py:
        return 2
    tool = os.path.join(root, "_toolkit", "tools", "tg-saved")
    src = args.media
    if ext in VIDEO_EXTS:
        ff = None
        for cand in ("ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe"):
            try:
                probe = subprocess.run([cand, "-version"], capture_output=True, check=False)
                if probe.returncode == 0:
                    ff = cand
                    break
            except OSError:
                continue
        if not ff:
            return fail("нет ffmpeg для извлечения звуковой дорожки")
        src = os.path.join(work, stem + ".wav")
        if not os.path.isfile(src):
            if run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", args.media,
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", src],
                   "извлечение аудио") is None:
                return 2
    if args.mode == "fast":
        model = os.path.join(root, "_tools", "tg-saved", "models", "gigaam")
        if not os.path.isdir(model):
            return fail("нет весов GigaAM: %s" % model)
        report = os.path.join(work, stem + ".gigaam.md")
        if run([py, "-X", "utf8", os.path.join(tool, "gigaam_long.py"),
                "--model-dir", model, "--out", report, src], "GigaAM") is None:
            return 2
        text = open(report, encoding="utf-8").read()
        if "gigaam-v3-e2e-ctc" not in text or "[ошибка окна:" in text:
            return fail("отчёт GigaAM не прошёл проверку: %s" % report)
        body = text.strip().rsplit("\n\n", 1)[-1].strip()
        if not body:
            return fail("GigaAM вернул пустой текст: %s" % args.media)
        with open(result, "w", encoding="utf-8", newline="") as f:
            f.write(origin("fast", "gigaam-v3-e2e-ctc", os.path.basename(args.media)) + body + "\n")
    elif args.mode == "eng":
        if run([py, "-X", "utf8", os.path.join(tool, "transcribe_audio.py"),
                "--audio", src, "--out", work, "--language", "en"], "Whisper") is None:
            return 2
        cands = [p for p in os.listdir(work) if p.endswith(".txt")]
        if not cands:
            return fail("Whisper не дал текстового отчёта в %s" % work)
        text = open(os.path.join(work, cands[0]), encoding="utf-8").read()
        if "язык `en`" not in text:
            return fail("отчёт Whisper не прошёл проверку")
        body = text.strip().rsplit("\n\n", 1)[-1].strip()
        if not body:
            return fail("Whisper вернул пустой текст: %s" % args.media)
        with open(result, "w", encoding="utf-8", newline="") as f:
            f.write(origin("eng", "whisper", os.path.basename(args.media)) + body + "\n")
    else:
        model = os.path.join(root, "_tools", "tg-saved", "models", "gigaam")
        if not os.path.isdir(model):
            return fail("нет весов GigaAM: %s" % model)
        second = os.path.join(work, "second-voice")
        fused = os.path.join(work, "fused")
        if run([py, "-X", "utf8", os.path.join(tool, "transcribe_dual.py"),
                "--audio", src, "--out", work, "--gigaam-dir", model], "dual-расшифровка") is None:
            return 2
        if run([py, "-X", "utf8", os.path.join(tool, "fuse_transcripts.py"),
                "--canon", work, "--second", second, "--out", fused], "fusion") is None:
            return 2
        # Решения по спорным регионам принимает только агент (не человек, не эвристика):
        # читает flagged.json, пишет decisions-1.json, повторяет эту же команду для завершения.
        import json as _json
        flagged_path = os.path.join(fused, "flagged.json")
        try:
            flagged = _json.load(open(flagged_path, encoding="utf-8"))
            flagged_ids = [item.get("id") for item in flagged] if isinstance(flagged, list) else [
                item.get("id") for item in flagged.get("items", [])]
        except (OSError, UnicodeError, ValueError):
            flagged_ids = []
        decisions_path = os.path.join(fused, "decisions-1.json")
        decided = set()
        if os.path.isfile(decisions_path):
            try:
                decided = {item.get("id") for item in
                           _json.load(open(decisions_path, encoding="utf-8")).get("items", [])}
            except (OSError, UnicodeError, ValueError, AttributeError):
                decided = set()
        missing = [i for i in flagged_ids if i not in decided]
        if missing:
            print("спорные регионы ждут решений агента: %s" % ", ".join(str(i) for i in missing))
            print("допиши %s и повтори команду" % decisions_path)
            return 3
        finish = [py, "-X", "utf8", os.path.join(tool, "finish_fusion.py"),
                  "--out", fused, "--apply"]
        if decided:
            finish += ["--decisions", decisions_path]
        if run(finish, "применение решений") is None:
            return 2
        cands = [p for p in os.listdir(fused) if p.endswith(".md")]
        if not cands:
            return fail("fusion не дал итогового Markdown в %s" % fused)
        text = open(os.path.join(fused, sorted(cands)[0]), encoding="utf-8").read().strip()
        if not text:
            return fail("fusion вернул пустой текст: %s" % args.media)
        with open(result, "w", encoding="utf-8", newline="") as f:
            f.write(origin("dual", "whisper+gigaam", os.path.basename(args.media)) + text + "\n")
    print("расшифровано: %s" % result)
    return 0


def transcribe_image(args, root, stem, result):
    script = os.path.join(root, "_toolkit", "ocr_win.ps1")
    out = os.path.join(os.path.dirname(result), stem + ".ocr.txt")
    cmd = ["powershell.exe", "-NoProfile", "-File", script,
           "-Path", args.media, "-Out", out, "-Lang", args.ocr_lang]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    words = re.search(r"done:\s*(\d+)\s+words", "\n".join(tail))
    if proc.returncode != 0 or not words or int(words.group(1)) == 0:
        return fail("локальный OCR недоступен или пуст — расшифруй зрением агента "
                    "по _toolkit/images-playbook.md и положи текст в %s" % result)
    text = open(out, encoding="utf-8").read().strip()
    with open(result, "w", encoding="utf-8", newline="") as f:
        f.write(origin("ocr", "windows-ocr", os.path.basename(args.media)) + text + "\n")
    print("распознано: %s" % result)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Расшифровка одного медиафайла в Markdown")
    ap.add_argument("--media", required=True)
    ap.add_argument("--mode", default="fast", choices=("fast", "dual", "eng"))
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--ocr-lang", default="ru-RU")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    media = os.path.abspath(a.media)
    if not os.path.isfile(media):
        return fail("нет медиафайла: %s" % a.media)
    ext = os.path.splitext(media)[1].lower()
    stem = os.path.splitext(os.path.basename(media))[0]
    result = os.path.join(os.path.dirname(media), stem + ".md")
    if os.path.exists(result):
        print("уже расшифровано: %s" % result)
        return 0
    work = os.path.join(os.path.dirname(media), "stt", stem + "-run")
    os.makedirs(work, exist_ok=True)
    args = argparse.Namespace(media=media, mode=a.mode, ocr_lang=a.ocr_lang)
    if ext in AUDIO_EXTS or ext in VIDEO_EXTS:
        return transcribe_audio_video(args, root, work, stem, result, ext)
    if ext in IMAGE_EXTS:
        return transcribe_image(args, root, stem, result)
    return fail("неизвестный тип медиа: %s" % ext)


if __name__ == "__main__":
    raise SystemExit(main())
