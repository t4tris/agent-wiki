#!/usr/bin/env python3
"""Расшифровка аудио двумя моделями: канон — whisper, второй голос — GigaAM.

Правило пайплайна (согласовано 2026-09-12):
  * **канонический транскрипт делает faster-whisper** — он сохраняет латиницу, а имена
    инструментов и английские термины (`OpenCode`, `reasoning`) в вики
    становятся сущностями и ссылками; потеря алфавита — это разрыв связи, а не опечатка;
  * **GigaAM идёт вторым голосом** — он быстрее на порядок, даёт пунктуацию и заглавные,
    и точнее на русской морфологии («табуляции» против «до буляции»);
  * **расхождения моделей — это список мест для прослушивания**, а не приговор одной из них.

    & ".venv-asr\\Scripts\\python.exe" _toolkit/tools/tg-saved/transcribe_dual.py --audio <файлы/папки> --out <папка stt> [--force]

Раскладка результата:
    <out>/<base>.txt              канон (whisper: medium, для записей < 180 с — small)
    <out>/second-voice/<base>.txt второй голос (GigaAM, окна по 30 с)
    <out>/divergence-<дата>.md    по-словная сверка обеих расшифровок
    <out>/stt-index.tsv           индекс, накапливается между запусками
"""
import argparse
import datetime
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

CHUNK, OVERLAP = 30, 1.0
FILLER = {"а", "аа", "э", "ээ", "эээ", "ну", "вот", "о", "у", "мм", "а-а", "э-э", "э-э-э"}
AUDIO = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".wma", ".mp4"}


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for rt, _, fs in os.walk(p):
                out += [os.path.join(rt, f) for f in fs if os.path.splitext(f)[1].lower() in AUDIO]
        elif os.path.splitext(p)[1].lower() in AUDIO:
            out.append(p)
    return sorted(out)


def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float((r.stdout or "0").strip())


def to_wav(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ar", "16000", "-ac", "1", dst], check=True)
    return dst


def tokens(t):
    t = re.sub(r"[^\w\s]", " ", (t or "").lower(), flags=re.UNICODE)
    return [w for w in t.split() if w and w not in FILLER]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gigaam-dir", default=r".\_tools\tg-saved\models\gigaam")
    ap.add_argument("--small-threshold", type=int, default=180)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    # Путь, которого нет, обязан называться своим именем: путь оболочки вида `/f/...` нативный интерпретатор
    # не понимает, и «аудио не найдено» вместо «путь не найден» читается как пустая папка.
    missing = [x for x in a.audio if not os.path.exists(x)]
    if missing:
        sys.exit("путь не найден: " + ", ".join(missing) +
                 " — запуск идёт нативным интерпретатором: путь должен быть нативным для Windows, например C:/... или D:/..., а не /f/...")
    files = collect(a.audio)
    if not files:
        sys.exit("аудио не найдено (пути существуют, аудиофайлов в них нет)")
    os.makedirs(a.out, exist_ok=True)
    second = os.path.join(a.out, "second-voice")
    os.makedirs(second, exist_ok=True)
    tmp = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Temp", "dual-asr")
    os.makedirs(tmp, exist_ok=True)

    from asr_engines import whisper_model as WhisperModel
    wh_models, giga = {}, None
    rows = []
    for i, f in enumerate(files, 1):
        base = os.path.splitext(os.path.basename(f))[0]
        canon_path, second_path = os.path.join(a.out, base + ".txt"), os.path.join(second, base + ".txt")
        d = dur(f)
        wav = os.path.join(tmp, base + ".wav")
        if not os.path.exists(wav):
            to_wav(f, wav)
        # 1. канон — whisper
        if not os.path.exists(canon_path) or a.force:
            name = "small" if d < a.small_threshold else "medium"
            if name not in wh_models:
                wh_models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            t0 = time.time()
            segs, _ = wh_models[name].transcribe(wav, language="ru", vad_filter=True, beam_size=5,
                                                 condition_on_previous_text=False)
            txt = " ".join(s.text.strip() for s in segs).strip()
            hdr = (f"# Транскрипт (канон, whisper `{name}`): {os.path.basename(f)}\n\n"
                   f"- длительность: {d:.0f} с\n"
                   f"- оговорка: машинное распознавание; канон выбран потому, что сохраняет латиницу\n\n")
            open(canon_path, "w", encoding="utf-8").write(hdr + txt + "\n")
            print(f"[{i}/{len(files)}] канон {base[:30]}: whisper {name}, {time.time()-t0:.0f} с, {len(txt)} зн", flush=True)
        # 2. второй голос — GigaAM окнами
        if not os.path.exists(second_path) or a.force:
            if giga is None:
                from asr_engines import onnx_model
                giga = onnx_model(path=a.gigaam_dir)
            t0 = time.time()
            parts, t = [], 0.0
            k = 0
            while t < d:
                k += 1
                seg = os.path.join(tmp, f"{base}.g{k:03d}.wav")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t - (OVERLAP if k > 1 else 0)):.2f}",
                                "-t", f"{CHUNK + OVERLAP:.2f}", "-i", f, "-ar", "16000", "-ac", "1", seg], check=True)
                parts.append(giga.recognize([seg])[0])
                t += CHUNK
            txt = " ".join(x for x in parts if x)
            hdr = (f"# Транскрипт (второй голос, GigaAM `gigaam-v3-e2e-ctc`): {os.path.basename(f)}\n\n"
                   f"- длительность: {d:.0f} с, окна по {CHUNK} с с перехлёстом {OVERLAP} с\n"
                   f"- оговорка: GigaAM латинские названия транслитерирует («опин код» вместо `opencode`) — не цитировать без сверки\n\n")
            open(second_path, "w", encoding="utf-8").write(hdr + txt + "\n")
            print(f"[{i}/{len(files)}] второй голос {base[:26]}: GigaAM {time.time()-t0:.1f} с, {len(txt)} зн", flush=True)
        rows.append((os.path.basename(f), round(d), canon_path, second_path))

    # 3. расхождения = список мест для прослушивания
    today = datetime.date.today().isoformat()
    pairs = []
    for name, _, cp, sp in rows:
        if not (os.path.exists(cp) and os.path.exists(sp)):
            continue
        c = tokens(re.sub(r"(?s)^.*?\n\n", "", open(cp, encoding="utf-8").read()))
        s = tokens(re.sub(r"(?s)^.*?\n\n", "", open(sp, encoding="utf-8").read()))
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, s, c, autojunk=False).get_opcodes():
            if tag == "replace" and (i2 - i1) <= 3 and (j2 - j1) <= 3:
                g, w = " ".join(s[i1:i2]), " ".join(c[j1:j2])
                if g and w and g != w:
                    pairs.append((name, g, w))
    # важное отделяем от морфологического шума: расхождения на латинице и на терминах — в первую таблицу
    lat = re.compile(r"[a-z]{2,}", re.IGNORECASE)
    term = re.compile("opencode|kilo|agent|skill|grace|mcp|codex|claude|cursor|reason|prompt|eval|sdd|api|cli", re.IGNORECASE)
    key_pairs = [(n, g, w) for n, g, w in pairs if lat.search(g) or lat.search(w) or term.search(g) or term.search(w)]
    rest = [(n, g, w) for n, g, w in pairs if (n, g, w) not in key_pairs]
    div = [f"# Расхождения распознавания ({today})", "",
           f"Канон — whisper, второй голос — GigaAM. Всего расхождений {len(pairs)}: важных {len(key_pairs)},",
           "остальное — морфология и служебные слова. Слушать нужно первую таблицу.", "",
           "## Важно: латиница, имена инструментов, термины", "", "| файл | GigaAM | канон (whisper) |", "|---|---|---|"]
    div += [f"| {n[:34]} | {g} | {w} |" for n, g, w in key_pairs]
    div += ["", "## Прочее (падежи, служебные слова — можно не слушать)", "", "| файл | GigaAM | канон (whisper) |", "|---|---|---|"]
    div += [f"| {n[:34]} | {g} | {w} |" for n, g, w in rest[:300]]
    open(os.path.join(a.out, f"divergence-{today}.md"), "w", encoding="utf-8").write("\n".join(div) + "\n")
    print(f"\nрасхождений: {len(pairs)} → {os.path.join(a.out, f'divergence-{today}.md')}")


if __name__ == "__main__":
    main()
