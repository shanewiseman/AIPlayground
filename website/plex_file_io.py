#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from threading import Lock

_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[str, Lock] = {}


def _path_lock(path: Path) -> Lock:
    normalized_path = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(normalized_path)
        if lock is None:
            lock = Lock()
            _PATH_LOCKS[normalized_path] = lock
        return lock


def write_text_locked(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _path_lock(path)
    temporary_path: Path | None = None
    with lock:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding=encoding,
                dir=path.parent,
                delete=False,
            ) as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
