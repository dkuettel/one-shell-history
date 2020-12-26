from dataclasses import dataclass, field, astuple
from typing import Optional, List, Iterable
import datetime
import json
from pathlib import Path
from contextlib import contextmanager

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
    failed_count: int


def aggregate_events_for_search(events: Iterable[Event]) -> Iterable[AggregatedEvent]:
    # TODO efficient enough? can really yield because stats are not ready before the end
    # also if we reverse, then Iterable is not really useful
    boring = {"ls", "lr", "ll", "htop", "v"}
    order = []
    aggregated_events = dict()
    for event in reversed(events):
        if event.command in boring:
            continue
        if event.command in aggregated_events:
            aggregated_event = aggregated_events[event.command]
            aggregated_event.occurence_count += 1
            if event.exit_code not in {0, None}:
                aggregated_event.failed_count += 1
        else:
            order.append(event.command)
            aggregated_events[event.command] = AggregatedEvent(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurence_count=1,
                failed_count=0 if event.exit_code in {0, None} else 1,
            )
    return [aggregated_events[i] for i in order]


def print_events(events: List[Event]):
    from tabulate import tabulate

    data = []

    for e in events:
        data.append([str(e.timestamp), e.command])

    print(tabulate(data, headers=["date", "command"]))


@dataclass
class EagerHistory:
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


@dataclass
class LazyHistory:
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
        with self._lock():
            disk = read_from_file(self.file, or_empty=True)
            self._events = merge([self._events, disk])
            write_to_file(self._events, self.file)
