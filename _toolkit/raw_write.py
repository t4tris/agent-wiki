import os
import subprocess
import tempfile


def read(path):
    with open(path, encoding="utf-8", newline="") as stream:
        return stream.read()


def atomic_write(path, text):
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".raw-write-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        try:
            os.replace(temporary, path)
        except PermissionError:
            os.chmod(path, 0o666)
            if os.name == "nt":
                subprocess.run(["attrib", "-r", path], capture_output=True, check=False)
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def plan(path, text, force, why, label, plans, issues, allow_existing=False):
    old = read(path) if os.path.exists(path) else None
    if old == text:
        return False
    if old is not None and not allow_existing and not force:
        issues.append(f"отказ: {label} {path}; требуется --force --why")
        return False
    if force and not why.strip():
        issues.append(f"отказ: --force требует непустого --why для {path}")
        return False
    plans.append((path, old, text, label))
    return True


def apply(plans):
    for path, old, text, _label in plans:
        current = read(path) if os.path.exists(path) else None
        if current != old:
            raise RuntimeError(f"файл изменился после планирования: {path}")
        atomic_write(path, text)
    return len(plans)
