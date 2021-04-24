import math
import os
import random
import time
from contextlib import contextmanager
from dataclasses import field
from pathlib import Path
from typing import Optional


class NoLock(Exception):
    pass


@contextmanager
def locked_file(file: Path, wait: Optional[float] = None, forever: bool = False):
    """lazy lockfile implementation, not sure if absolutely failsafe
    file is the original file to be locked, ".lock" is appended automatically
    """

    # this might produce race conditions?
    file.parent.mkdir(parents=True, exist_ok=True)

    lfile = f"{file}.lock"

    if forever:
        assert wait is None
        wait = math.inf

    starttime = time.time()
    fd = None
    while fd is None:
        try:
            fd = os.open(path=lfile, flags=os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            if wait is None:
                raise NoLock()
            elif (time.time() - starttime) > wait:
                raise NoLock()
            time.sleep(random.uniform(0.5, 1.0))

    try:
        yield

    finally:

        try:
            os.unlink(lfile)
        except:
            pass

        try:
            os.close(fd)
        except:
            pass


def ffield(default_factory):
    return field(default_factory=default_factory)
