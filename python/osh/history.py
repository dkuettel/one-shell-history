from dataclasses import dataclass
from typing import Optional, List, Iterable
import datetime
import json
from pathlib import Path


@dataclass(order=True, frozen=True)
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
        return jd

    @classmethod
    def from_json_dict(cls, jd):
        jd = dict(jd)
        jd["timestamp"] = datetime.datetime.fromisoformat(jd["timestamp"])
        return cls(**jd)


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
