#!/usr/bin/env python3
"""Сравнение русских ASR на одних и тех же записях: GigaAM против faster-whisper.

    & ".venv-asr\\Scripts\\python.exe" _toolkit/tools/tg-saved/compare_asr.py --model-dir ./_tools/tg-saved/models/gigaam --out compare.md

Запускать интерпретатором `.venv-asr`, в котором установлены `faster-whisper` и `onnx-asr`.
`ffmpeg` и `ffprobe` должны быть доступны через PATH.

Что делает:
  * берёт набор аудио (голосовые из выгрузки Telegram + записи диктовки Handy);
  * прогоняет каждую модель на каждом файле;
  * считает длину текста, совпадение по словам и наличие ключевых терминов;
  * пишет markdown-отчёт с обоими вариантами рядом — чтобы судить по факту, а не по впечатлению.

Ничего не перезаписывает в источниках вики: это замер.
"""
import argparse
import difflib
import os
import re
import subprocess
import sys
import time

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_stdio()


def wav_cmd(src, dst):
    return ["ffmpeg", "-v", "error", "-y", "-i", src, "-ar", "16000", "-ac", "1", dst]


def to_wav16(src, tmpdir):
    """GigaAM и whisper ждут 16 кГц моно; голосовые Telegram — .ogg/.opus."""
    if src.lower().endswith(".wav"):
        return src
    dst = os.path.join(tmpdir, os.path.splitext(os.path.basename(src))[0] + ".wav")
    if not os.path.exists(dst):
        subprocess.run(wav_cmd(src, dst), check=True)
    return dst


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


TERMS = ["references", "troubleshooting", "opencode", "kilo", "grace", "mcp",
         "skill", "скилл", "промпт", "prompt", "табуляц", "контекст", "агент", "codex", "claude"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--handy", required=True, help="папка записей диктофона (путь у каждой машины свой)")
    ap.add_argument("--telegram", required=True, help="папка голосовых из выгрузки Telegram Desktop")
    ap.add_argument("--whisper", default="medium", help="размер модели whisper: small под ~3 мин, medium выше")
    a = ap.parse_args()

    tmp = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "asr-compare")
    os.makedirs(tmp, exist_ok=True)
    files = []
    if os.path.isdir(a.telegram):
        files += sorted(os.path.join(a.telegram, f) for f in os.listdir(a.telegram) if f.endswith(".ogg"))[:10]
    if os.path.isdir(a.handy):
        files += sorted(os.path.join(a.handy, f) for f in os.listdir(a.handy) if f.endswith(".wav"))[:3]
    print(f"файлов к сравнению: {len(files)}", flush=True)

    from asr_engines import onnx_model
    from asr_engines import whisper_model as WhisperModel
    print("загружаю GigaAM…", flush=True)
    giga = onnx_model(path=a.model_dir)
    print(f"загружаю faster-whisper {a.whisper}…", flush=True)
    wh = WhisperModel(a.whisper, device="cpu", compute_type="int8")

    rows = []
    for i, f in enumerate(files, 1):
        wav = to_wav16(f, tmp)
        t0 = time.time()
        try:
            g_txt = " ".join(giga.recognize([wav]))
        except (OSError, RuntimeError, ValueError) as ex:
            g_txt = f"ОШИБКА: {ex}"
        g_t = time.time() - t0
        t0 = time.time()
        segs, info = wh.transcribe(wav, language="ru", vad_filter=True, beam_size=5, condition_on_previous_text=False)
        w_txt = " ".join(s.text.strip() for s in segs).strip()
        w_t = time.time() - t0
        dur = getattr(info, "duration", 0) or 0
        ratio = difflib.SequenceMatcher(None, norm(g_txt), norm(w_txt)).ratio()
        terms = {t: (norm(g_txt).count(t), norm(w_txt).count(t)) for t in TERMS}
        rows.append({"file": os.path.basename(f), "dur": round(dur, 1), "giga": g_txt, "whisper": w_txt,
                     "t_giga": round(g_t, 1), "t_wh": round(w_t, 1), "agree": round(ratio, 3), "terms": terms})
        print(f"[{i}/{len(files)}] {rows[-1]['file'][:34]} | {dur:.0f} с | GigaAM {g_t:.0f} с | whisper {w_t:.0f} с | схожесть {ratio:.2f}", flush=True)

    lines = ["# GigaAM против faster-whisper на одних и тех же записях", "",
             "Замер: одинаковые файлы, обе модели на CPU, никаких правок вручную.", "",
             "| файл | длит. | GigaAM, с | whisper, с | схожесть текстов |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['file']} | {r['dur']} | {r['t_giga']} | {r['t_wh']} | {r['agree']} |")
    lines += ["", "## Термины (GigaAM / whisper)", "",
              "| термин | " + " | ".join(r["file"][:18] for r in rows) + " |",
              "|---" * (len(rows) + 1) + "|"]
    for term in TERMS:
        cells = [f"{r['terms'][term][0]} / {r['terms'][term][1]}" for r in rows]
        if any(c != "0 / 0" for c in cells):
            lines.append(f"| {term} | " + " | ".join(cells) + " |")
    lines += ["", "## Тексты рядом", ""]
    for r in rows:
        lines += [f"### {r['file']} ({r['dur']} с)", "",
                  "**GigaAM:**", r["giga"] or "—", "",
                  f"**faster-whisper {a.whisper}:**", r["whisper"] or "—", ""]
    open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\nотчёт:", a.out)


if __name__ == "__main__":
    main()
