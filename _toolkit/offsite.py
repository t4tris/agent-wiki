"""Удалённая копия проекта: отправка и проверка, что на той стороне ровно наш HEAD.

Зачем: диск один, а девять циклов работы лежат на нём. Резервная копия на другом логическом
диске того же физического носителя проблему не решает — нужна копия вне машины.
Каждая отправка проверяется обратным чтением (`git ls-remote`) и пишется строкой в реестр;
свежесть реестра проверяет линтер (§36), поэтому «копия есть» — это проверка, а не обещание.

Запуск: python3 _toolkit/offsite.py push|status [--wiki .]
"""
import argparse
import datetime
import os
import subprocess
import sys

import toolkit

LOG = "audit/offsite-log.tsv"
LOG_DISPLAY = os.path.join(toolkit.AREA, "audit", "offsite-log.tsv")
COLS = ("date", "remote", "branch", "commit", "files", "pack", "verified")


def git(root, *args):
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, errors="replace", check=False)
    return r.returncode, (r.stdout + r.stderr).strip()


def read_log(root):
    p = toolkit.area(root, LOG)
    if not os.path.exists(p):
        return [COLS]
    return [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]


def cmd_push(root, branch=None):
    rc, out = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch or out
    rc, remote = git(root, "remote", "get-url", "origin")
    if rc != 0:
        sys.exit("нет remote origin — сначала настрой удалённую копию")
    rc, out = git(root, "push", "origin", branch)
    print(out)
    if rc != 0:
        sys.exit("push не прошёл: удалённая копия не обновлена")
    rc, head = git(root, "rev-parse", f"refs/heads/{branch}")
    rc, remote_head = git(root, "ls-remote", "origin", f"refs/heads/{branch}")
    remote_sha = remote_head.split()[0] if remote_head else ""
    verified = "ok" if remote_sha == head else f"РАСХОЖДЕНИЕ {remote_sha[:8]}"
    rc2, files = git(root, "ls-files")
    n_files = len([l for l in files.split("\n") if l.strip()])
    # размер истории: count-objects отдаёт килобайты (size + size-pack) — переводим в мегабайты
    rc3, pack = git(root, "count-objects", "-v")
    def _kb(prefix):
        for l in pack.split("\n"):
            if l.startswith(prefix):
                try:
                    return int(l.split(":")[1].strip())
                except (TypeError, ValueError):
                    return 0
        return 0
    pack_m = [f"{( _kb('size') + _kb('size-pack') ) / 1024:.1f} MB"]
    row = (datetime.date.today().isoformat(), remote, branch, head[:12], str(n_files),
           (pack_m[0].split(":")[-1].strip() if pack_m else "?"), verified)
    p = toolkit.area(root, LOG)
    new_file = not os.path.exists(p) or not open(p, encoding="utf-8").read().strip()
    with open(p, "a", encoding="utf-8") as f:
        if new_file:
            f.write("\t".join(COLS) + "\n")     # шапка обязательна: без неё реестр не читается (§36)
        f.write("\t".join(row) + "\n")
    # Push — это резервная копия, а НЕ доставка аудитору (ответ десятого разбора, вопрос 1).
    # Раньше здесь стоял mark-sent, и день с несколькими push ломал инвариант «одна отправленная
    # строка на дату»; самопометка «доставлено» к тому же была самозаверением. Доставка фиксируется
    # отдельным событием отправки с составом посылки — см. строку реестра `sent-event-model`.
    print(f"отправлено: {branch} → {remote} | локально {head[:8]} | на той стороне {remote_sha[:8]} | {verified}")
    print(f"запись добавлена в {LOG_DISPLAY}")


def cmd_status(root):
    rows = read_log(root)
    if len(rows) < 2:
        print("удалённая копия ещё не подтверждалась ни разу")
        return
    last = dict(zip(rows[0].split("\t"), rows[-1].split("\t")))
    d = datetime.date.fromisoformat(last["date"])
    print(f"последняя подтверждённая отправка: {last['date']} ({(datetime.date.today() - d).days} дн. назад) "
          f"| {last['remote']} {last['branch']} @ {last['commit']} | проверка: {last['verified']}")


def main():
    ap = argparse.ArgumentParser(description="Удалённая копия: отправка с обратной проверкой.")
    ap.add_argument("action", choices=("push", "status"))
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--branch", default=None)
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    (cmd_push if a.action == "push" else cmd_status)(root, a.branch) if a.action == "push" else cmd_status(root)


if __name__ == "__main__":
    main()
