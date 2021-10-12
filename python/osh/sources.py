from __future__ import annotations

import datetime
import itertools
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from osh.history import Event
from osh.osh_files import (
    OshFileReader,
    create_osh_file,
    read_osh_file,
    read_osh_legacy_file,
)
from osh.zsh_files import read_zsh_file


class IncrementalSource:
    """
    the interface here and with the other source is a bit fragile
    you need to first call needs_reload
    and then if true, you need to call get_all_events
    otherwise call get_new_events
    needs_reload says only if it needs reloading since last time you called needs_reload
    not since last time you did any of get_*_events
    """

    def __init__(self, path: Path):
        self.path = path
        self.archived = ArchivedSources(path / "archive")
        self.active = ActiveSources(path)

    def needs_reload(self):
        # TODO very brittle :/ see class doc, maybe there is a more robust way without much code?
        # otherwise call it check instead of state-y?
        archived = self.archived.needs_reload()
        active = self.active.needs_reload()
        return archived or active

    def get_all_events(self):
        archived_osh, archived_other = self.archived.get_all_events()
        active_osh = self.active.get_all_events()
        events = merge_other_into_main(archived_other, archived_osh + active_osh)
        return events

    def get_new_events(self):
        return self.active.get_new_events()


@dataclass(frozen=True)
class DiscoveredArchiveFile:
    path: Path
    size: int
    mtime: float

    @classmethod
    def from_path(cls, path: Path):
        stat = path.stat()
        return cls(path, stat.st_size, stat.st_mtime)


class ArchivedSources:
    def __init__(self, path: Path):
        self.path = path
        self.last_check = -math.inf
        self.min_delay = 10
        self.files = None

    def needs_reload(self):
        now = time.time()
        if now - self.last_check < self.min_delay:
            return False
        self.last_check = now
        files = self.discover_files()
        if files == self.files:
            return False
        self.files = files
        return True

    def get_all_events(self):
        osh, other = [], []
        for dfile in self.files:
            try:
                if dfile.path.suffix == ".osh":
                    osh.extend(read_osh_file(dfile.path))
                elif dfile.path.suffix == ".osh_legacy":
                    osh.extend(read_osh_legacy_file(dfile.path))
                elif dfile.path.suffix == ".zsh_history":
                    other.extend(read_zsh_file(dfile.path))
                else:
                    raise Exception(f"unknown type of history {dfile.path}")
            except FileNotFoundError:
                pass
        return osh, other

    def discover_files(self):
        glob = itertools.chain(
            self.path.glob("**/*.osh"),
            self.path.glob("**/*.osh_legacy"),
            self.path.glob("**/*.zsh_history"),
        )
        return {DiscoveredArchiveFile.from_path(p) for p in glob}


class ActiveSources:
    def __init__(self, path: Path):
        self.path = path
        self.last_check = -math.inf
        self.min_delay = 10
        self.files = None

    def needs_reload(self):
        now = time.time()
        if now - self.last_check < self.min_delay:
            return False
        self.last_check = now
        files = self.discover_files()
        if files == self.files:
            return False
        self.files = files
        return True

    def get_all_events(self):
        self.readers = [OshFileReader(f) for f in self.files]
        return list(self.get_new_events())

    def get_new_events(self):
        for reader in self.readers:
            yield from reader.read_events()

    def discover_files(self):
        return set(self.path.glob("*.osh"))


class Source:
    def as_list(self) -> list[Event]:
        raise NotImplementedError()

    def as_sorted(self) -> list[Event]:
        # TODO if it's too slow delegate to subclasses and make smart unions?
        # also this assumes no real duplicate problem, then timestamps are unique enough for a stable ordering
        return sorted(self.as_list(), key=lambda e: e.timestamp)

    def mtime(self) -> float:
        """return a time.time()-like modified time, or just like Path.stat().st_mtime, relative to the context of this source"""
        raise NotImplementedError()


def merge_other_into_main(other, main):
    """
    we generally assume that the 'main' source has no collisions with itself
    typically 'main' comes from osh sources, and you dont run multiple osh's on the same machine in parallel
    in contrast, 'other' typically comes from traditional history implementations, like zsh's own history
    they might have run in parallel, since you can have both zsh and osh record history at the same time
    in short, duplicates within 'main' are not dealt with, but duplicates within 'other' and against 'main' are dealt with
    """

    if len(other) == 0:
        return main

    # zsh and bash seem to use a posix timestamp floored to seconds
    # therefore these are the seconds when 'main' was recording
    # and we dont need to use 'other' to complete our history
    # (this is a somehwat crude heuristics in order to be efficient)
    covered_seconds = {math.floor(event.timestamp.timestamp()) for event in main}

    # TODO some easy stats here to see if there is much after coming in, or restrict to last 10 days
    # but how to warn, or return stats, or handle it with user notification?
    # print(f"{min(events_seconds)=}")
    # print(f"{len(merge_events)=}")
    additional = [
        event
        for event in other
        if math.floor(event.timestamp.timestamp()) not in covered_seconds
    ]
    # print(f"{len(merge_events)=}")
    # merge_events_after = [event for event in merge_events if math.floor(event.timestamp.timestamp())>min(events_seconds)]
    # print(f"{len(merge_events_after)=}")

    return main + additional


if __name__ == "__main__":
    source = IncrementalSource(Path("histories"))
    print(f"{source.needs_reload()=}")
    events = source.get_all_events()
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
    print(f"{source.needs_reload()=}")
    new_events = list(source.get_new_events())
    print(f"{len(new_events)=}")
