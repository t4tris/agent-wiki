#!/usr/bin/env python3
"""GigaAM на длинных записях: режем на куски, распознаём, склеиваем.

GigaAM v3 (CTC) обучена на коротких фрагментах и на длинном входе обрывается:
позиционные эмбеддинги ограничены ~5000 кадров, и модель либо падает, либо
возвращает огрызок. Поэтому длинное аудио режется на окна по 30 секунд с
небольшим перехлёстом, каждое окно распознаётся отдельно, затем текст склеивается.

    & ".venv-asr\\Scripts\\python.exe" _toolkit/tools/tg-saved/gigaam_long.py --model-dir ./_tools/tg-saved/models/gigaam --out report.md <файлы…>
"""
import argparse
import os
import subprocess
import time
import sys

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_stdio()

CHUNK = 30
OVERLAP = 1.0


def dur_of(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float((p.stdout or "0").strip() or 0)


def split(path, outdir):
    d = dur_of(path)
    base = os.path.splitext(os.path.basename(path))[0]
    parts = []
    t = 0.0
    i = 0
    while t < d:
        i += 1
        seg = os.path.join(outdir, f"{base}.part{i:03d}.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t - (OVERLAP if i > 1 else 0)):.2f}",
                        "-t", f"{CHUNK + OVERLAP:.2f}", "-i", path, "-ar", "16000", "-ac", "1", seg],
                       check=True)
        parts.append(seg)
        t += CHUNK
    return d, parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()

    tmp = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "gigaam-chunks")
    os.makedirs(tmp, exist_ok=True)

    from asr_engines import onnx_model
    model = onnx_model(path=a.model_dir)

    lines = ["# GigaAM на длинных записях: окна по 30 секунд", "",
             f"Модель: gigaam-v3-e2e-ctc (int8, локально). Окно {CHUNK} с, перехлёст {OVERLAP} с.", ""]
    for f in a.files:
        d, parts = split(f, tmp)
        t0 = time.time()
        texts = []
        for p in parts:
            try:
                texts.append(model.recognize([p])[0])
            except (OSError, RuntimeError, ValueError) as ex:
                texts.append(f"[ошибка окна: {str(ex)[:60]}]")
        txt = " ".join(t for t in texts if t)
        lines += [f"## {os.path.basename(f)} ({d:.0f} с, окон {len(parts)})", "",
                  f"Обработка: {time.time()-t0:.1f} с", "", txt, ""]
        print(f"{os.path.basename(f)[:36]:38} {d:6.0f} с | окон {len(parts):2d} | {time.time()-t0:5.1f} с | {len(txt)} знаков", flush=True)
    open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\nотчёт:", a.out)


if __name__ == "__main__":
    main()
