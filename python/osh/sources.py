from __future__ import annotations

import datetime
import itertools
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from osh.history import Event
from osh.osh_files import (
    OshFileChangedMuch,
    OshFileReader,
    create_osh_file,
    read_osh_file,
    read_osh_legacy_file,
)
from osh.zsh_files import read_zsh_file


class ArchivedSources:
    def __init__(self, path: Path, globs: str):
        self.path = path
        self.globs = globs
        self.revision = 0
        self.events = []
        self.signature = set()
        self.last_check = -math.inf
        self.min_delay = 10

    def refresh(self):

        now = time.time()
        if now - self.last_check < self.min_delay:
            return
        self.last_check = now

        def sig(f):
            assert not f.is_symlink()
            stat = f.stat()
            return (f, stat.st_size, stat.st_mtime)

        signature = {sig(f) for glob in self.globs for f in self.path.glob(glob)}

        if signature == self.signature:
            return
        self.revision += 1

        self.events = []
        self.signature = set()
        for f, size, mtime in signature:
            try:
                self.events.extend(read_any_file(f))
                self.signature.add((f, size, mtime))
            except FileNotFoundError:
                pass


class ActiveSources:
    def __init__(self, path: Path, local_source: Optional[Path] = None):
        self.path = path
        self.local_source = local_source
        self.revision = 0
        self.events = []
        self.signature = set()
        self.readers = []
        self.last_check = -math.inf
        self.min_delay = 1

    def refresh(self):

        now = time.time()
        if now - self.last_check < self.min_delay:
            return
        self.last_check = now

        signature = set(self.path.glob("*.osh"))
        if self.local_source is not None:
            signature |= {self.local_source}
        signature = {p.resolve() for p in signature}

        if signature == self.signature:
            try:
                for r in self.readers:
                    self.events.extend(r.read_events())
            except OshFileChangedMuch:
                pass
            else:
                return

        self.revision += 1
        self.events = []
        self.signature = set()
        self.readers = []

        for f in signature:
            try:
                r = OshFileReader(f)
                self.events.extend(r.read_events())
                self.signature.add(f)
                self.readers.append(r)
            except FileNotFoundError:
                pass


def read_any_file(file: Path) -> list[Event]:
    if file.suffix == ".osh":
        return read_osh_file(file)
    elif file.suffix == ".osh_legacy":
        return read_osh_legacy_file(file)
    elif file.suffix == ".zsh_history":
        return read_zsh_file(file)
    else:
        raise Exception(f"unknown type of history {file}")
