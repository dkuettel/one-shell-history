import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from osh.history import AggregatedEvent, SearchConfig
from osh.osh_files import OshFileChangedMuch


class EventFilter:
    def has_changed(self) -> bool:
        return False

    def discard(self, event) -> bool:
        return False


class UserEventFilter(EventFilter):
    def __init__(self, path: Path):
        self.path = path
        self.last_mtime = None
        self.last_size = None
        self.ignored_commands = set()
        self.boring_patterns = set()

    def has_changed(self):
        try:
            stat = self.path.stat()
            if (self.last_mtime, self.last_size) == (stat.st_mtime, stat.st_size):
                return False

            self.last_mtime = stat.st_mtime
            self.last_size = stat.st_size

            config = json.loads(self.path.read_text())
            ignored_commands = set(config.get("ignored-commands", []))
            boring_patterns = {re.compile(p) for p in config.get("boring-patterns", [])}

        except FileNotFoundError:
            ignored_commands = set()
            boring_patterns = set()

        if (
            self.ignored_commands == ignored_commands
            and self.boring_patterns == boring_patterns
        ):
            return False

        self.ignored_commands = ignored_commands
        self.boring_patterns = boring_patterns
        return True

    def discard(self, event):
        if event.command in self.ignored_commands:
            return True
        for pattern in self.boring_patterns:
            if pattern.fullmatch(event.command):
                return True
        return False


class UniqueCommandsQuery:
    def __init__(self, source, event_filter: EventFilter = EventFilter()):
        self.source = source
        self.event_filter = event_filter
        self.aggs = {}

    def as_most_often_first(self, filter_failed_at: Optional[float] = None):

        needs_reload = self.source.needs_reload()
        filter_has_changed = self.event_filter.has_changed()

        if needs_reload or filter_has_changed:
            events = self.source.get_all_events()
            self.aggs = {}
        else:
            try:
                events = self.source.get_new_events()
            except OshFileChangedMuch:
                events = self.source.get_all_events()
                self.aggs = {}

        for event in events:
            self.update(event)

        relevants = self.aggs.values()

        if filter_failed_at is not None:
            relevants = (
                e
                for e in relevants
                if (e.fail_ratio is None) or (e.fail_ratio < filter_failed_at)
            )

        relevants = sorted(relevants, key=lambda e: -e.occurence_count)
        return relevants

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


def test():
    import time
    from pathlib import Path

    from osh.sources import IncrementalSource

    source = IncrementalSource(Path("histories"))
    event_filter = UserEventFilter(
        Path("~/.one-shell-history/search.json").expanduser()
    )
    rels = UniqueCommandsQuery(source, event_filter)

    dt = time.time()
    events = list(reversed(rels.as_most_often_first()))
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {time.time()-dt}")

    print()
    dt = time.time()
    events = list(reversed(rels.as_most_often_first()))
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {time.time()-dt}")


if __name__ == "__main__":
    test()
