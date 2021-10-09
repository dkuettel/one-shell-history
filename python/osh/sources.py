from __future__ import annotations

import datetime
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from osh.zsh_files import read_zsh_file

from osh.history import Event
from osh.osh_files import (
    FileChangedMuch,
    OshFileReader,
    create_osh_file,
    read_osh_file,
    read_osh_legacy_file,
)


class Source:
    def as_list(self) -> list[Event]:
        raise NotImplementedError()

    def as_sorted(self) -> list[Event]:
        # TODO if it's too slow delegate to subclasses and make smart unions?
        # also this assumes no real duplicate problem, then timestamps are unique enough for a stable ordering
        return sorted(self.as_list(), key=lambda e: e.timestamp)


class UnionSource(Source):
    def __init__(self, sources: list[Source]):
        self.sources = sources

    def as_list(self) -> list[Event]:
        return [event for source in self.sources for event in source.as_list()]


class MergeInSource(Source):
    """
    we generally assume that the 'main' source has no collisions with itself
    typically 'main' comes from osh sources, and you dont run multiple osh's on the same machine in parallel
    in contrast, 'other' typically comes from traditional history implementations, like zsh's own history
    they might have run in parallel, since you can have both zsh and osh record history at the same time
    in short, duplicates within 'main' are not dealt with, but duplicates within 'other' and against 'main' are dealt with
    """

    def __init__(self, main: Source, other: Source):
        self.main = main
        self.other = other

    def as_list(self):

        events = self.main.as_list()
        candidates = self.other.as_list()

        if len(candidates) == 0:
            return events

        # zsh and bash seem to use a posix timestamp floored to seconds
        # therefore these are the seconds when 'main' was recording
        # and we dont need to use 'other' to complete our history
        # (this is a somehwat crude heuristics in order to be efficient)
        covered_seconds = {math.floor(event.timestamp.timestamp()) for event in events}

        # TODO some easy stats here to see if there is much after coming in, or restrict to last 10 days
        # but how to warn, or return stats, or handle it with user notification?
        # print(f"{min(events_seconds)=}")
        # print(f"{len(merge_events)=}")
        additional = [
            event
            for event in candidates
            if math.floor(event.timestamp.timestamp()) not in covered_seconds
        ]
        # print(f"{len(merge_events)=}")
        # merge_events_after = [event for event in merge_events if math.floor(event.timestamp.timestamp())>min(events_seconds)]
        # print(f"{len(merge_events_after)=}")

        return events + additional


class OshSource(Source):
    def __init__(self, path: Path):
        self.path = path

    def as_list(self) -> list[Event]:
        try:
            return read_osh_file(self.path)
        except FileNotFoundError:
            return []


class IncrementalOshSource(Source):
    def __init__(self, path: Path):
        self.path = path
        self.reader = None
        self.events = None

    def as_list(self):
        if self.reader is None:
            self.reader = OshFileReader(self.path)
            self.events = []

        try:
            self.events.extend(self.reader.read_events())
            return self.events

        except FileNotFoundError:
            self.reader = None
            self.events = []
            return self.events

        except FileChangedMuch:
            self.reader = None
            self.events = None
            return self.as_list()


class OshLegacySource(Source):
    def __init__(self, path: Path):
        self.path = path

    def as_list(self):
        try:
            return read_osh_legacy_file(self.path)
        except FileNotFoundError:
            # TODO i'm not sure now, return [] or last data here? [] would be probably better
            return []


class ZshSource(Source):
    def __init__(self, path: Path):
        self.path = path

    def as_list(self):
        try:
            return read_zsh_file(self.path)
        except FileNotFoundError:
            return []


if __name__ == "__main__":
    sources = [OshLegacySource()]
    merge_sources = [ZshSource()]
    source = UnionSource(sources, merge_sources)
    events = source.as_list()
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
