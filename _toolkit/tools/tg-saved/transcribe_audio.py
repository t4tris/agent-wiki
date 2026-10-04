#!/usr/bin/env python3
"""Транскрипция аудио из выгрузки Telegram локальным Whisper (CPU).

    & ".venv-asr\\Scripts\\python.exe" _toolkit/tools/tg-saved/transcribe_audio.py --audio <папка или файлы> --out <папка вывода> [--model small]

Зачем: в сохранёнках попадаются голосовые сообщения. Выгрузка кладёт их в
`voice_messages/` (и иногда `files/` как .oga/.mp3). Текст голосового — такой же
источник, как сообщение, но это машинное распознавание, а не авторский текст,
поэтому в отчёте всегда указывается модель, длительность и оговорка о точности.

Идемпотентно: готовые .txt не перезаписываются (если не передан --force).
Рядом пишется `stt-index.tsv`: файл, длительность, секунд обработки, язык, модель, первые слова.
"""
import argparse
import datetime
import os
import sys
import time

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_stdio()

AUDIO_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma", ".mp4"}


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for rt, _, fs in os.walk(p):
                for f in fs:
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                        out.append(os.path.join(rt, f))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXT:
            out.append(p)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small", help="модель для коротких записей")
    ap.add_argument("--model-long", default="medium", help="модель для записей длиннее порога")
    ap.add_argument("--long-seconds", type=int, default=180, help="порог длинной записи, секунды")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    # Путь, которого нет, обязан называться своим именем: путь оболочки вида `/f/...` нативный интерпретатор
    # не понимает, и «аудио не найдено» вместо «путь не найден» читается как пустая папка.
    missing = [x for x in a.audio if not os.path.exists(x)]
    if missing:
        sys.exit("путь не найден: " + ", ".join(missing) +
                 " — запуск идёт нативным интерпретатором: путь должен быть нативным для Windows, например C:/... или D:/..., а не /f/...")
    files = collect(a.audio)
    if not files:
        sys.exit("аудиофайлов не найдено")

    import subprocess as sp

    from asr_engines import whisper_model as WhisperModel

    def duration_s(path):
        out = sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60, check=True)
        return float((out.stdout or "0").strip() or 0)

    models, dur_cache = {}, {}
    def get_model(name):
        if name not in models:
            t = time.time()
            models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            print(f"модель {name} (cpu, int8) загружена за {time.time()-t:.1f} с", flush=True)
        return models[name]

    print(f"файлов: {len(files)}; короткие — {a.model}, длиннее {a.long_seconds} с — {a.model_long}", flush=True)

    rows, done, skipped = [], 0, 0
    for i, path in enumerate(files, 1):
        base = os.path.splitext(os.path.basename(path))[0]
        txt_path = os.path.join(a.out, base + ".txt")
        dur_hint = dur_cache.setdefault(path, duration_s(path))
        use_model = a.model_long if dur_hint > a.long_seconds else a.model
        if os.path.exists(txt_path) and not a.force:
            head = open(txt_path, encoding="utf-8").read(600)
            if f"модель `{use_model}`" in head:      # готовая расшифровка той же моделью
                skipped += 1
                continue
        t1 = time.time()
        segments, info = get_model(use_model).transcribe(path, language=a.language, vad_filter=True,
                                                         beam_size=5, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        dur = getattr(info, "duration", 0.0)
        header = (f"# Транскрипт: {os.path.basename(path)}\n\n"
                  f"- файл: `{os.path.abspath(path).replace(os.sep, '/')}`\n"
                  f"- длительность: {dur:.1f} с\n"
                  f"- распознано: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}, "
                  f"модель `{use_model}` (локально, cpu/int8), язык `{a.language}`, VAD включён\n"
                  f"- оговорка: машинное распознавание; перед цитированием сверяться с аудио\n\n")
        open(txt_path, "w", encoding="utf-8").write(header + text + "\n")
        rows.append({"file": os.path.basename(path), "seconds": round(dur, 1),
                     "process": round(time.time() - t1, 1), "lang": a.language, "model": use_model,
                     "chars": len(text), "head": text[:120]})
        done += 1
        print(f"[{i}/{len(files)}] {base}: {dur:.0f} с аудио → {len(text)} знаков за {time.time()-t1:.0f} с", flush=True)

    idx = os.path.join(a.out, "stt-index.tsv")
    old = []
    if os.path.exists(idx):                     # индекс накапливается: прошлые строки не теряются
        old = [l for l in open(idx, encoding="utf-8").read().strip().split("\n")[1:] if l.strip()]
    seen = {l.split("\t")[0] for l in old}
    new = ["\t".join(str(r[k]) for k in ("file", "seconds", "process", "lang", "model", "chars", "head")) for r in rows if r["file"] not in seen]
    with open(idx, "w", encoding="utf-8") as f:
        f.write("file\tseconds\tprocess_s\tlang\tmodel\tchars\thead\n")
        f.write("\n".join(old + new) + "\n")
    print(f"\nготово: расшифровано {done}, пропущено готовых {skipped}, индекс {idx}")


if __name__ == "__main__":
    main()
