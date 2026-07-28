"""Cross-platform advisory lock released automatically on process death."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class RunLockedError(RuntimeError):
    pass


def _lock_file(file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RunLockedError("run is already active") from exc
        return

    import fcntl

    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise RunLockedError("run is already active") from exc


def _unlock_file(file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def run_lock(lock_directory: Path, run_id: str) -> Iterator[Path]:
    lock_directory.mkdir(parents=True, exist_ok=True)
    safe_name = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    lock_path = lock_directory / f"{safe_name}.lock"
    file = lock_path.open("a+b")
    try:
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        _lock_file(file)
        try:
            yield lock_path
        finally:
            _unlock_file(file)
    finally:
        file.close()
