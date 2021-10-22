from __future__ import annotations

import datetime
import socket as sockets
from dataclasses import asdict, dataclass
from functools import cache, cached_property
from itertools import islice
from pathlib import Path
from typing import Optional

from osh import defaults
from osh.event_filters import EventFilter, maybe_create_event_filter_config_file
from osh.history import Event, History
from osh.osh_files import append_event_to_osh_file, create_osh_file
from osh.queries import (
    BackwardsQuery,
    UniqueCommand,
    UniqueCommandsQuery,
    query_next_event,
    query_previous_event,
)


class Osh:
    def __init__(self, dot: Path = defaults.dot):
        self.dot = dot

        self.dot.mkdir(parents=True, exist_ok=True)
        (self.dot / defaults.archive).mkdir(parents=True, exist_ok=True)
        (self.dot / defaults.active).mkdir(parents=True, exist_ok=True)
        maybe_create_event_filter_config_file(self.dot / defaults.event_filters)
        if not (self.dot / defaults.local).exists():
            target = self.dot / defaults.active / f"{sockets.gethostname()}.osh"
            if not target.exists():
                create_osh_file(target)
                (self.dot / defaults.local).symlink_to(target.relative_to(self.dot))

    @cached_property
    def source(self):
        # TODO generally we want to call osh a history
        # rename History to a source? also move then maybe and no circular problems again
        return History(self.dot)

    @cache
    def unique_commands_query(self, filter_ignored: bool):
        if filter_ignored:
            return UniqueCommandsQuery(
                self.source, EventFilter(self.dot / defaults.event_filters)
            )
        else:
            return UniqueCommandsQuery(self.source)

    def search(self, filter_failed_at, filter_ignored):
        yield from self.unique_commands_query(filter_ignored).generate_results(
            filter_failed_at
        )

    @cached_property
    def backwards_query(self):
        return BackwardsQuery(self.source)

    def search_backwards(
        self,
        session_id: Optional[str] = None,
        session_start: Optional[datetime.datetime] = None,
    ):
        # TODO make that a function in queries? since it's not state-based, just on list of events?
        yield from self.backwards_query.generate_results(
            session=session_id,
            no_older_than=session_start,
        )

    def previous_event(
        self,
        timestamp: datetime.datetime,
        prefix: Optional[str] = None,
        ignore: Optional[str] = None,
        session_id: Optional[str] = None,
        session_start: Optional[datetime.datetime] = None,
    ):
        self.source.refresh()
        return query_previous_event(
            self.source.events, timestamp, prefix, ignore, session_id, session_start
        )

    def next_event(
        self,
        timestamp: datetime.datetime,
        prefix: Optional[str] = None,
        ignore: Optional[str] = None,
        session_id: Optional[str] = None,
        session_start: Optional[datetime.datetime] = None,
    ):
        self.source.refresh()
        return query_next_event(
            self.source.events, timestamp, prefix, ignore, session_id, session_start
        )

    def append_event(self, event: Event):
        # TODO create if not there? generally just work out of the box if anything is missing
        append_event_to_osh_file(self.dot / defaults.local, event)

    def get_statistics(self) -> Statistics:
        return Statistics.from_source(self.source)


@dataclass
class Statistics:
    count: int = 0
    earliest: Optional[datetime.datetime] = None
    latest: Optional[datetime.datetime] = None
    success_rate: Optional[float] = None

    @classmethod
    def from_source(cls, source: History):
        source.refresh()
        count = len(source.events)
        if count == 0:
            return cls()
        return cls(
            count=count,
            earliest=min(e.timestamp for e in source.events),
            latest=max(e.timestamp for e in source.events),
            success_rate=sum(e.exit_code in {0, None} for e in source.events) / count,
        )

    def to_json_dict(self):
        jd = asdict(self)
        if jd["earliest"] is not None:
            jd["earliest"] = jd["earliest"].isoformat()
        if jd["latest"] is not None:
            jd["latest"] = jd["latest"].isoformat()
        return jd

    @classmethod
    def from_json_dict(cls, jd):
        if jd["earliest"] is not None:
            jd["earliest"] = datetime.datetime.fromisoformat(jd["earliest"])
        if jd["latest"] is not None:
            jd["latest"] = datetime.datetime.fromisoformat(jd["latest"])
        return cls(**jd)
