import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from osh.event_filters import EventFilter
from osh.history import AggregatedEvent, SearchConfig
from osh.sources import HistorySource


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
            occurence_count=self.occurence_count,
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
        source: HistorySource,
        event_filter: Optional[EventFilter] = None,
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

        yield from sorted(uniques, key=lambda u: -u.occurence_count)

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

        if self.event_filter and self.event_filter.discard(event):
            return

        u = self.uniques.get(event.command, None)

        if u is None:
            u = UniqueCommand(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurence_count=1,
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
            u.occurence_count += 1
            if event.exit_code is not None:
                u.known_exit_count += 1
                if event.exit_code != 0:
                    u.failed_exit_count += 1
            u.folders.update({event.folder})


class BackwardsQuery:
    def __init__(self, source: HistorySource):
        self.source = source

    def generate_results(self, session: Optional[str]):

        self.source.refresh()

        # TODO restrict to max 1 month back or something?
        if session is None:
            yield from reversed(self.source.events)
        else:
            yield from (e for e in reversed(self.source.events) if e.session == session)


def test():

    source = HistorySource(Path("histories"))
    event_filter = UserEventFilter(
        Path("~/.one-shell-history/search.json").expanduser()
    )
    query = UniqueCommandsQuery(source, event_filter)

    for i in range(3):
        print()
        dt = time.time()
        events = list(reversed(list(query.generate_results())))
        dt = time.time() - dt
        events = events[-10:]
        for e in events:
            print(e.occurence_count, e.command)
        print(f"took {dt}")


if __name__ == "__main__":
    test()
