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
    OshFileChangedMuch,
    OshFileReader,
    create_osh_file,
    read_osh_file,
    read_osh_legacy_file,
)
from osh.zsh_files import read_zsh_file


class HistorySource:
    def __init__(self, path: Path):
        self.path = path
        self.archived_osh = ArchivedSources(
            path / "archive",
            ["**/*.osh", "**/*.osh_legacy"],
        )
        self.archived_other = ArchivedSources(
            path / "archive",
            ["**/*.zsh_history"],
        )
        self.active = ActiveSources(path)
        self.revision = 0
        self.events = []
        self.signature = (
            self.archived_osh.revision,
            self.archived_other.revision,
            self.active.revision,
        )
        self.active_length = 0

    def refresh(self):
        self.archived_osh.refresh()
        self.archived_other.refresh()

        signature = (
            self.archived_osh.revision,
            self.archived_other.revision,
            self.active.revision,
        )
        active_length = len(self.active.events)

        if signature == self.signature and active_length == self.active_length:
            return

        if signature == self.signature and active_length != self.active_length:
            new_events = sorted(
                self.active.events[self.active_length :],
                key=lambda e: e.timestamp,
            )
            if (len(self.events) == 0) or (
                self.events[-1].timestamp <= new_events[0].timestamp
            ):
                self.events.extend(new_events)
                self.active_length = active_length
                return

        self.revision += 1
        self.signature = signature
        self.active_length = active_length

        events = merge_other_into_main(
            self.archived_other.events,
            self.archived_osh.events + self.active.events,
        )
        self.events = sorted(events, key=lambda e: e.timestamp)


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
    def __init__(self, path: Path):
        self.path = path
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
        assert all(not f.is_symlink() for f in signature)

        if signature == self.signature:
            try:
                for r in self.readers:
                    self.events.extend(r.get_new_events())
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
                self.events.extend(r.get_new_events())
                self.signature.add(f)
                self.readers.add(r)
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
    history = HistorySource(Path("histories"))
    history.refresh()
    print(f"{history.revision=}")
    events = history.events
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
    history.refresh()
    print(f"{history.revision=}")
    events = history.events
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
