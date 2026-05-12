import json
import os
import shutil
import tempfile
import threading
from pathlib import Path


_locks = {}
_locks_guard = threading.Lock()


def _lock_for(path):
    resolved = str(Path(path).resolve())
    with _locks_guard:
        if resolved not in _locks:
            _locks[resolved] = threading.RLock()
        return _locks[resolved]


def data_path(path):
    path = Path(path)
    data_dir = os.getenv("DATA_DIR")
    if data_dir and not path.is_absolute() and path.parts and path.parts[0] == "data":
        return Path(data_dir, *path.parts[1:])
    return path


def seed_data_file(target_path, original_path):
    original_path = Path(original_path)
    if target_path.exists() or target_path.resolve() == original_path.resolve():
        return
    if not original_path.exists():
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original_path, target_path)


def load_json(path, default):
    target_path = data_path(path)
    lock = _lock_for(target_path)
    with lock:
        seed_data_file(target_path, path)
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default


def save_json(path, data, *, indent=4):
    target_path = data_path(path)
    lock = _lock_for(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with lock:
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            dir=target_path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent)
                f.write("\n")
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
