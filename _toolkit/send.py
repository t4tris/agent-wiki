#!/usr/bin/env python3
"""Отправка цикла: одна посылка, один путь.

    python3 _toolkit/send.py --wiki . [--note "…"] [--letter _staging/audit/auditor-report-<дата>.md]

Что делает, по шагам:

1. собирает посылку (`merge_package.py`) — один прогон, чтобы письмо и пакет жили из одного состояния;
2. берёт хеш машинного манифеста `package-index-<дата>.json` — это замок пары «письмо ↔ посылка»;
3. вписывает в шапку письма цикла `package`, `package_manifest`, `package_manifest_sha256`;
4. пересобирает посылку и **проверяет, что хеш манифеста не изменился**: он не должен зависеть от письма,
   иначе получится петля (письмо печатает хеш манифеста, манифест перечисляет письмо);
5. дописывает строку в `_staging/audit/sent-artifacts.tsv`: письмо и его хеш, манифест и его хеш,
   посылка и её хеш — «что именно ушло» видно по составу, а не по памяти;
6. печатает, что осталось сделать руками (коммит и отправка в удалённую копию; push отправкой не считается).

Отправляется только посылка: письмо отдельным файлом не уходит — у него нет своего пути отправки.
"""
import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys

import toolkit

SENT = "audit/sent-artifacts.tsv"
SENT_DISPLAY = os.path.join(toolkit.AREA, "audit", "sent-artifacts.tsv")
COLUMNS = ("date", "file", "sha256", "note", "manifest", "manifest_sha256", "package", "package_sha256")


def read(path):
    return open(path, encoding="utf-8", errors="replace").read()


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run(root, *args):
    r = subprocess.run([sys.executable, toolkit.script(args[0]), *args[1:]],
                       cwd=root, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(r.stdout[-800:] if r.stdout else "", r.stderr[-800:] if r.stderr else "")
        sys.exit(f"шаг {' '.join(args)} упал")
    return r.stdout


def stamp_letter(path, package, manifest, manifest_hash):
    """Вписывает в шапку письма ссылку на посылку и хеш манифеста; уже стоящие ключи заменяет."""
    t = read(path)
    m = re.match(r"^---\r?\n(.*?)\r?\n---", t, re.DOTALL)
    if not m:
        sys.exit("у письма нет шапки: подписывать нечего")
    fm = m.group(1)
    for key, val in (("package", package), ("package_manifest", manifest),
                     ("package_manifest_sha256", manifest_hash)):
        if re.search(rf"(?m)^{key}:", fm):
            fm = re.sub(rf"(?m)^{key}:.*$", f"{key}: {val}", fm, count=1)
        else:
            fm = fm.rstrip("\n") + f"\n{key}: {val}"
    open(path, "w", encoding="utf-8", newline="").write(t[:m.start(1)] + fm + t[m.end(1):])


def main():
    ap = argparse.ArgumentParser(description="Отправка цикла: собрать посылку, заморозить письмо, записать состав.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--letter", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    date = a.date
    audit = toolkit.area(root, "audit")
    sent_names = set()
    reg = toolkit.area(root, SENT)
    if os.path.exists(reg):
        for row in read(reg).splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) >= 2:
                sent_names.add(os.path.basename(cols[1]))

    def current_letter():
        """Письмо даты, которое уходит: отправленное заморожено, поэтому берётся новейшее не отправленное."""
        cand = [f for f in os.listdir(audit)
                if re.match(r"auditor-(report|response(-\d+[a-z]?)?)-" + re.escape(date) + r"\.md$", f)]
        # Только новейшее письмо даты: старое письмо в роли новейшего — подмена, а не запасной вариант.
        return max(cand, key=lambda f: os.path.getmtime(os.path.join(audit, f))) if cand else ""

    if a.letter:
        letter = a.letter if os.path.isabs(a.letter) else os.path.join(root, a.letter)
    else:
        cur = current_letter()
        if not cur:
            sys.exit(f"за {date} нет письма: ни отчёта, ни ответа")
        if cur in sent_names:
            sys.exit(f"письмо за {date} уже записано отправленным ({cur}); новое письмо — новое имя; старое вместо нового не подставляется")
        letter = os.path.join(audit, cur)
    if not os.path.exists(letter):
        sys.exit(f"нет письма: {letter}")
    manifest = toolkit.area(root, "audit", f"package-index-{date}.json")
    package = toolkit.area(root, "audit", f"audit-package-{date}.md")

    run(root, "merge_package.py", "--wiki", root)
    if not os.path.exists(manifest):
        sys.exit(f"нет манифеста {manifest}: сначала собери пакет")
    mh = sha256(manifest)
    letter_hash_before = sha256(letter)
    stamp_letter(letter, os.path.basename(package), os.path.basename(manifest), mh)
    run(root, "merge_package.py", "--wiki", root)
    mh_after = sha256(manifest)
    if mh_after != mh:
        sys.exit(f"хеш манифеста изменился после подписи письма ({mh[:12]} → {mh_after[:12]}): письмо попало "
                 f"в груз, петля хешей. Проверь исключение в merge_package.py")

    letter_hash = sha256(letter)
    package_hash = sha256(package) if os.path.exists(package) else ""
    label = "обложка цикла" if letter.endswith(f"auditor-report-{date}.md") else "письмо"
    note = a.note or f"{label} {date}: числа заморожены; состав отправки — в манифесте {os.path.basename(manifest)}"
    row = {"date": date, "file": os.path.relpath(letter, toolkit.area(root)).replace("\\", "/"),
           "sha256": letter_hash, "note": note, "manifest": os.path.basename(manifest),
           "manifest_sha256": mh, "package": os.path.basename(package), "package_sha256": package_hash}
    path = toolkit.area(root, SENT)
    lines = [l for l in read(path).splitlines() if l.strip()] if os.path.exists(path) else []
    head = lines[0].split("\t") if lines else list(COLUMNS)
    rows = [l.split("\t") for l in lines[1:]]
    if list(head) != list(COLUMNS):                      # старый заголовок — дописываем колонки, старые строки не теряем
        for r in rows:
            r += [""] * (len(COLUMNS) - len(r))
        head = list(COLUMNS)
    same = [r for r in rows if len(r) > 1 and r[1] == row["file"]]
    if same and same[-1][2] == letter_hash:
        print(f"это письмо уже записано как отправленное ({letter_hash[:12]}…) — новой строки не будет")
    else:
        if same:
            print(f"повторная отправка того же письма: было {same[-1][2][:12]}…, стало {letter_hash[:12]}… "
                  f"(прежняя строка остаётся; отзыва не предусмотрено — различие видно по хешам)")
        rows.append([row[c] for c in COLUMNS])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(head) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")

    if not a.quiet:
        print(f"посылка: {os.path.basename(package)} ({os.path.getsize(package) / 1024:.1f} КБ) | "
              f"sha256 {package_hash[:12]}…")
        print(f"манифест: {os.path.basename(manifest)} | sha256 {mh[:12]}…")
        print(f"письмо: {row['file']} | sha256 {letter_hash[:12]}…"
              + ("" if letter_hash == letter_hash_before else " (в шапку вписан хеш манифеста)"))
        print(f"записано в {SENT_DISPLAY} | строк отправок за {date}: "
              f"{sum(1 for r in rows if r[0] == date)}")
        print("дальше руками: коммит (хук проверит линтер) и `python3 _toolkit/tasks.py offsite` — "
              "push это копия, отправка им не помечается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
