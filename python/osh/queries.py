import datetime
import itertools
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from osh.event_filters import EventFilter, NoEventFilter
from osh.history import Event, History


@dataclass
class UniqueCommand:
    most_recent_timestamp: datetime.datetime
    command: str
    occurrence_count: int
    known_exit_count: int
    failed_exit_count: int
    folders: Counter
    most_recent_folder: Optional[str]

    def to_json_dict(self):
        return dict(
            most_recent_timestamp=self.most_recent_timestamp.isoformat(),
            command=self.command,
            occurrence_count=self.occurrence_count,
            known_exit_count=self.known_exit_count,
            failed_exit_count=self.failed_exit_count,
            folders=self.folders,
            most_recent_folder=self.most_recent_folder,
        )

    @classmethod
    def from_json_dict(cls, jd):
        jd = dict(jd)
        jd["most_recent_timestamp"] = datetime.datetime.fromisoformat(
            jd["most_recent_timestamp"]
        )
        jd["folders"] = Counter(jd["folders"])
        return cls(**jd)

    @property
    def fail_ratio(self) -> Optional[float]:
        if self.known_exit_count == 0:
            return None
        return self.failed_exit_count / self.known_exit_count


class UniqueCommandsQuery:
    def __init__(
        self,
        source: History,
        event_filter: EventFilter = NoEventFilter(),
    ):
        self.source = source
        self.event_filter = event_filter
        self.uniques = {}
        self.source_revision = None
        self.source_length = None
        self.filter_revision = None

    def generate_results(self, filter_failed_at: Optional[float] = None):

        self.refresh()

        uniques = self.uniques.values()

        if filter_failed_at is not None:
            uniques = (
                u
                for u in uniques
                if (u.fail_ratio is None) or (u.fail_ratio < filter_failed_at)
            )

        yield from sorted(uniques, key=lambda u: -u.occurrence_count)

    def refresh(self):

        self.source.refresh()
        self.event_filter.refresh()

        if (
            (self.source_revision == self.source.revision)
            and (self.source_length == len(self.source.events))
            and (self.filter_revision == self.event_filter.revision)
        ):
            return

        if (
            (self.source_revision == self.source.revision)
            and (self.source_length < len(self.source.events))
            and (self.filter_revision == self.event_filter.revision)
        ):
            events = self.source.events[self.source_length :]
        else:
            events = self.source.events
            self.uniques = {}

        for e in events:
            self.update(e)

        self.source_revision = self.source.revision
        self.source_length = len(self.source.events)
        self.filter_revision = self.event_filter.revision

    def update(self, event):

        if self.event_filter.discard(event):
            return

        u = self.uniques.get(event.command, None)

        if u is None:
            u = UniqueCommand(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurrence_count=1,
                known_exit_count=0 if event.exit_code is None else 1,
                failed_exit_count=0 if event.exit_code in {0, None} else 1,
                folders=Counter({event.folder}),
                most_recent_folder=event.folder,
            )
            self.uniques[event.command] = u

        else:
            if event.timestamp > u.most_recent_timestamp:
                u.most_recent_timestamp = event.timestamp
                u.most_recent_folder = event.folder
            u.occurrence_count += 1
            if event.exit_code is not None:
                u.known_exit_count += 1
                if event.exit_code != 0:
                    u.failed_exit_count += 1
            u.folders.update({event.folder})


class BackwardsQuery:
    def __init__(self, source: History):
        self.source = source

    def generate_results(
        self,
        session: Optional[str] = None,
        no_older_than: Optional[datetime.datetime] = None,
    ):
        self.source.refresh()

        for e in reversed(self.source.events):
            if no_older_than is not None and e.timestamp < no_older_than:
                break
            if session is not None and e.session != session:
                continue
            yield e


def query_previous_event(
    events: list[Event],
    timestamp: datetime.datetime,
    prefix: Optional[str],
    session_id: Optional[str] = None,
    session_start: Optional[datetime.datetime] = None,
):
    events = reversed(events)
    events = itertools.dropwhile(lambda e: e.timestamp >= timestamp, events)
    if session_start is not None:
        events = itertools.takewhile(lambda e: e.timestamp >= session_start, events)
    if prefix is not None:
        events = (e for e in events if e.command.startswith(prefix))
    if session_id is not None:
        events = (e for e in events if e.session == session_id)

    return next(events, None)


def query_next_event(
    events: list[Event],
    timestamp: datetime.datetime,
    prefix: Optional[str],
    session_id: Optional[str] = None,
    session_start: Optional[datetime.datetime] = None,
):
    events = reversed(events)
    if session_start is None or timestamp > session_start:
        events = itertools.takewhile(lambda e: e.timestamp > timestamp, events)
    else:
        events = itertools.takewhile(lambda e: e.timestamp >= session_start, events)
    if prefix is not None:
        events = (e for e in events if e.command.startswith(prefix))
    if session_id is not None:
        events = (e for e in events if e.session == session_id)
    candidate = None
    for e in events:
        candidate = e
    return candidate


def test():

    source = History(Path("histories"))
    event_filter = EventFilter(Path("~/.one-shell-history/search.json").expanduser())
    query = UniqueCommandsQuery(source, event_filter)

    for i in range(3):
        print()
        dt = time.time()
        events = list(reversed(list(query.generate_results())))
        dt = time.time() - dt
        events = events[-10:]
        for e in events:
            print(e.occurrence_count, e.command)
        print(f"took {dt}")


if __name__ == "__main__":
    test()
