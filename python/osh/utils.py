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


def seconds_to_slang(seconds: float) -> str:
    if seconds < 60:
        return f"{round(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{round(minutes)}m"
    hours = minutes / 60
    if hours < 10:
        return f"{round(hours,1)}h"
    if hours < 24:
        return f"{round(hours)}h"
    days = hours / 24
    if days < 2:
        return f"{round(days,1)}d"
    if days < 300:
        return f"{round(days)}d"
    years = days / 365
    if years < 2:
        return f"{round(years,1)}y"
    return f"{round(years)}y"


def str_mark_trailing_spaces(s) -> str:
    l = len(s)
    s = s.rstrip(" ")
    return s + "•" * (l - len(s))
