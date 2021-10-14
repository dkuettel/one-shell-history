import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from osh.history import AggregatedEvent, SearchConfig
from osh.sources import HistorySource


class EventFilter:
    def __init__(self):
        self.revision = 0

    def refresh(self):
        pass

    def discard(self, event) -> bool:
        return False


class UserEventFilter(EventFilter):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.signature = None
        self.last_check = -math.inf
        self.min_delay = 5
        self.ignored_commands = set()
        self.boring_patterns = set()

    def refresh(self):
        try:
            path = self.path.resolve()
            stat = path.stat()
            signature = (path, stat.st_mtime, stat.st_size)
            if signature == self.signature:
                return
            self.signature = signature
            config = json.loads(path.read_text())
            ignored_commands = set(config.get("ignored-commands", []))
            boring_patterns = {re.compile(p) for p in config.get("boring-patterns", [])}
        except FileNotFoundError:
            ignored_commands = set()
            boring_patterns = set()

        if (
            self.ignored_commands == ignored_commands
            and self.boring_patterns == boring_patterns
        ):
            return

        self.revision += 1
        self.ignored_commands = ignored_commands
        self.boring_patterns = boring_patterns

    def discard(self, event):
        if event.command in self.ignored_commands:
            return True
        for pattern in self.boring_patterns:
            if pattern.fullmatch(event.command):
                return True
        return False


class UniqueCommandsQuery:
    def __init__(
        self,
        source: HistorySource,
        event_filter: EventFilter = EventFilter(),
    ):
        self.source = source
        self.event_filter = event_filter
        self.aggs = {}
        self.source_revision = None
        self.source_length = None
        self.filter_revision = None

    def generate_events(self, filter_failed_at: Optional[float] = None):

        self.refresh()

        events = self.aggs.values()

        if filter_failed_at is not None:
            events = (
                e
                for e in events
                if (e.fail_ratio is None) or (e.fail_ratio < filter_failed_at)
            )

        yield from sorted(events, key=lambda e: -e.occurence_count)

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
            self.aggs = {}

        for e in events:
            self.update(e)

        self.source_revision = self.source.revision
        self.source_length = len(self.source.events)
        self.filter_revision = self.event_filter.revision

    def update(self, event):

        if self.event_filter.discard(event):
            return

        agg = self.aggs.get(event.command, None)

        if agg is None:
            agg = AggregatedEvent(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurence_count=1,
                known_exit_count=0 if event.exit_code is None else 1,
                failed_exit_count=0 if event.exit_code in {0, None} else 1,
                folders=Counter({event.folder}),
                most_recent_folder=event.folder,
            )
            self.aggs[event.command] = agg

        else:
            if event.timestamp > agg.most_recent_timestamp:
                agg.most_recent_timestamp = event.timestamp
                agg.most_recent_folder = event.folder
            agg.occurence_count += 1
            if event.exit_code is not None:
                agg.known_exit_count += 1
                if event.exit_code != 0:
                    agg.failed_exit_count += 1
            agg.folders.update({event.folder})


class BackwardsQuery:
    def __init__(self, source: HistorySource):
        self.source = source

    def generate_events(self, session: Optional[str]):

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

    dt = time.time()
    events = list(reversed(list(query.generate_events())))
    dt = time.time() - dt
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {dt}")

    print()
    dt = time.time()
    events = list(reversed(list(query.generate_events())))
    dt = time.time() - dt
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {dt}")

    print()
    dt = time.time()
    events = list(reversed(list(query.generate_events())))
    dt = time.time() - dt
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {dt}")


if __name__ == "__main__":
    test()
