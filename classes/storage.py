import json
import os
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


def load_json(path, default):
    lock = _lock_for(path)
    with lock:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default


def save_json(path, data, *, indent=4):
    lock = _lock_for(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    with lock:
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{Path(path).name}.",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent)
                f.write("\n")
            os.replace(temp_path, path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
