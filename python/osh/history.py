from dataclasses import dataclass
from typing import Optional, List, Iterable
import datetime
import json
from pathlib import Path


@dataclass(order=True, frozen=True)
class Entry:
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


def make(entries: Iterable[Entry]) -> List[Entry]:
    return sorted(set(entries))


def merge(histories: List[List[Entry]]) -> List[Entry]:
    return make({entry for history in histories for entry in history})


def read_from_file(file: Path, or_empty: bool = False) -> List[Entry]:
    if or_empty and not file.exists():
        return []
    history = json.loads(file.read_text())
    return [Entry.from_json_dict(entry) for entry in history]


def write_to_file(history: List[Entry], file: Path):
    json_dict = [entry.to_json_dict() for entry in history]
    json_str = json.dumps(json_dict, indent=2)
    file.write_text(json_str)
