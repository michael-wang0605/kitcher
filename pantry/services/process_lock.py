"""OS-held worker lock; a process exit releases it without a stale PID file."""

import os
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import CommandError


@contextmanager
def process_lock(name, message):
    path = settings.VAR_DIR / name
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CommandError(message) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def import_worker_lock():
    with process_lock(
        "import-worker.lock",
        "An import worker is already running for this data directory.",
    ):
        yield
