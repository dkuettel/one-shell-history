import datetime
import json
import math
import re
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import astuple, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from osh.sources import ActiveSources, ArchivedSources


@dataclass(frozen=True)
class Event:
    timestamp: datetime.datetime
    command: str
    duration: Optional[float] = None
    exit_code: Optional[int] = None
    folder: Optional[str] = None
    machine: Optional[str] = None
    session: Optional[str] = None

    def __post_init__(self):
        assert self.timestamp.tzinfo is datetime.timezone.utc

    @classmethod
    def from_now(cls, **kwargs):
        return cls(timestamp=datetime.datetime.now(datetime.timezone.utc), **kwargs)

    def to_json_dict(self):
        jd = dict()
        jd["timestamp"] = self.timestamp.isoformat()
        jd["command"] = self.command
        if self.duration is not None:
            jd["duration"] = self.duration
        if self.exit_code is not None:
            jd["exit_code"] = self.exit_code
        if self.folder is not None:
            jd["folder"] = self.folder
        if self.machine is not None:
            jd["machine"] = self.machine
        if self.session is not None:
            jd["session"] = self.session
        return jd

    @classmethod
    def from_json_dict(cls, jd):
        jd = dict(jd)
        jd["timestamp"] = datetime.datetime.fromisoformat(jd["timestamp"])
        return cls(**jd)


class History:
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


def test():
    history = History(Path("histories"))
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


if __name__ == "__main__":
    test()
