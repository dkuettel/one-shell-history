from dataclasses import dataclass, field, astuple
from typing import Optional, List, Iterable
import datetime
import json
from pathlib import Path
from contextlib import contextmanager
from functools import total_ordering


@total_ordering
@dataclass(frozen=True)
class Event:
    timestamp: datetime.datetime
    command: str
    duration: Optional[int] = None
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
        return jd

    @classmethod
    def from_json_dict(cls, jd):
        jd = dict(jd)
        jd["timestamp"] = datetime.datetime.fromisoformat(jd["timestamp"])
        return cls(**jd)

    def __lt__(self, other):
        assert type(self) is Event
        assert type(other) is Event
        for a, b in zip(astuple(self), astuple(other)):
            if a != b:
                # we make None < anything else
                if a is None:
                    return True
                if b is None:
                    return False
                return a < b
        return False


def make(events: Iterable[Event]) -> List[Event]:
    return sorted(set(events))


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
    file.write_text(json_str)


@dataclass
class FromFile:
    filename: Path = Path("zsh-history.json")
    events: Optional[List[Event]] = None

    @contextmanager
    def edit(self):
        assert self.events is None
        self.events = read_from_file(self.filename, or_empty=True)
        yield
        write_to_file(self.events, self.filename)
        self.events = None

    def insert_event(self, event: Event):
        assert self.events is not None
        self.events = merge([self.events, [event]])


def generate_pruned_for_search(history: Iterable[Event]) -> Iterable[Event]:
    used = set()
    for event in reversed(history):
        if event.command not in used:
            used.add(event.command)
            yield event
