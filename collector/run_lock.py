"""Cross-process inventory run exclusion."""

import errno
import fcntl
from contextlib import contextmanager

from flask import current_app


class RunAlreadyActive(Exception):
    """Another inventory run holds the file lock."""


@contextmanager
def run_lock():
    with open(current_app.config["RUN_LOCK_PATH"], "a+b") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise RunAlreadyActive("An inventory run is already in progress") from error
            raise
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
