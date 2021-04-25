import datetime
import json
import re
from contextlib import contextmanager
from dataclasses import astuple, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from osh.utils import locked_file


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
        # TODO does this format support milliseconds and all?
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


def make(events: Iterable[Event]) -> List[Event]:
    """
    this produces the canonical sorting that should be stable
    if there is a hash collision it might not be completely stable
    currently we use this as the authority on serialized representation
    but in the end working with a set would probably be the most robust thing
    """
    return sorted(set(events), key=lambda e: (e.timestamp, hash(e)))


def merge(histories: List[List[Event]]) -> List[Event]:
    return make({event for history in histories for event in history})


def read_from_file(file: Path, or_empty: bool = False) -> List[Event]:
    if or_empty and not file.exists():
        return []
    history = json.loads(file.read_text())
    return [Event.from_json_dict(event) for event in history]


def write_to_file(history: List[Event], file: Path):
    json_dict = [event.to_json_dict() for event in history]
    json_str = json.dumps(json_dict, indent=2)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json_str)


@dataclass
class FromFile:
    file: Path = Path("~/.one-shell-history/events.json").expanduser()
    events: Optional[List[Event]] = None

    @contextmanager
    def lock(self):
        from osh.utils import locked_file

        assert self.events is None

        with locked_file(self.file, wait=10):
            self.events = read_from_file(self.file, or_empty=True)
            try:
                yield
                write_to_file(self.events, self.file)
            finally:
                self.events = None

    def insert_event(self, event: Event):
        assert self.events is not None
        self.events = merge([self.events, [event]])


@dataclass
class AggregatedEvent:
    most_recent_timestamp: datetime.datetime
    command: str
    occurence_count: int
    known_exit_count: int
    failed_exit_count: int

    def to_json_dict(self):
        return dict(
            most_recent_timestamp=self.most_recent_timestamp.isoformat(),
            command=self.command,
            occurence_count=self.occurence_count,
            known_exit_count=self.known_exit_count,
            failed_exit_count=self.failed_exit_count,
        )

    @classmethod
    def from_json_dict(cls, jd):
        jd = dict(jd)
        jd["most_recent_timestamp"] = datetime.datetime.fromisoformat(
            jd["most_recent_timestamp"]
        )
        return cls(**jd)

    @property
    def fail_ratio(self) -> Optional[float]:
        if self.known_exit_count == 0:
            return None
        return self.failed_exit_count / self.known_exit_count


def aggregate_events(
    events: Iterable[Event],
    filter_failed_at: Optional[float] = 1.0,
) -> Iterable[AggregatedEvent]:

    # TODO efficient enough? can really yield because stats are not ready before the end
    # also if we reverse, then Iterable is not really useful, unless it's a smarter iterable, some can do it fast?
    # or the in-memory list could be reverse already

    config = SearchConfig()
    aggregated = {}

    for event in reversed(events):
        if not config.event_is_useful(event):
            continue

        if event.command in aggregated:
            agg = aggregated[event.command]
            agg.occurence_count += 1
            if event.exit_code is not None:
                agg.known_exit_count += 1
                if event.exit_code != 0:
                    agg.failed_exit_count += 1
        else:
            aggregated[event.command] = AggregatedEvent(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurence_count=1,
                known_exit_count=0 if event.exit_code is None else 1,
                failed_exit_count=0 if event.exit_code in {0, None} else 1,
            )

    # ordered as most recent event first
    ordered = list(aggregated.values())

    if filter_failed_at is not None:
        ordered = [
            e
            for e in ordered
            if (e.fail_ratio is None) or (e.fail_ratio < filter_failed_at)
        ]

    return ordered


def print_events(events: List[Event]):
    from tabulate import tabulate

    data = []

    for e in events:
        data.append([str(e.timestamp), e.command])

    print(tabulate(data, headers=["date", "command"]))


class History:
    pass


@dataclass
class EagerHistory(History):
    file: Path = Path("~/.one-shell-history/events.json").expanduser()

    def _lock(self):
        return locked_file(self.file, wait=10)

    def insert_event(self, event: Event):
        with self._lock():
            events = read_from_file(self.file, or_empty=True)
            events.append(event)
            events = make(events)
            write_to_file(events, self.file)

    def as_list(self) -> List[Event]:
        with self._lock():
            events = read_from_file(self.file, or_empty=True)
        return events

    def sync(self):
        pass


@dataclass
class LazyHistory(History):
    file: Path = Path("~/.one-shell-history/events.json").expanduser()

    def __post_init__(self):
        with self._lock():
            self._events = read_from_file(self.file, or_empty=True)

    def _lock(self):
        return locked_file(self.file, wait=10)

    def insert_event(self, event: Event):
        self._events = merge([self._events, [event]])

    def as_list(self) -> List[Event]:
        return list(self._events)

    def sync(self):
        print("start lazy sync ...", flush=True)
        with self._lock():
            disk = read_from_file(self.file, or_empty=True)
            disk_count_before = len(disk)
            self._events = merge([self._events, disk])
            write_to_file(self._events, self.file)
            disk_count_after = len(self._events)
            disk_count_added = disk_count_after - disk_count_before
        print(f"... lazy sync done, {disk_count_added} events added", flush=True)


class SearchConfig:
    _empty_config = {
        "version": "1",
        "ignored-commands": [],
        "boring-patterns": [],
    }

    def __init__(self):
        self._path = Path("~/.one-shell-history/search.json").expanduser()
        self._read()

    def _read(self):
        if self._path.exists():
            self._config = dict(self._empty_config)
            self._config.update(json.loads(self._path.read_text()))
        else:
            self._config = dict(self._empty_config)
        assert self._config["version"] == "1"
        self._write()

    def _write(self):
        self._path.write_text(json.dumps(self._config, indent=4))

    def event_is_useful(self, event: Event) -> bool:
        # TODO hacky, should check if config file has changed the first time
        if event.command in self._config["ignored-commands"]:
            return False
        for pattern in self._config["boring-patterns"]:
            if re.fullmatch(pattern, event.command):
                return False
        return True

    def add_ignored_command(self, command: str):
        self._read()
        self._config["ignored-commands"].append(command)
        self._write()
