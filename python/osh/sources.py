from __future__ import annotations

import datetime
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from osh.history import Event


class Source:
    # TODO list or set? in a way set makes more sense? unless we want to expect a certain order already
    # like assume it's deduplicated list ordered in ascending time? that's sofar the normal format in the rest of the code
    def as_list(self) -> list[Event]:
        raise NotImplementedError()


class Union(Source):
    def __init__(
        self,
        sources: list[Source],
        merge_sources: Optional[list[Source]] = None,
    ):
        self.sources = sources
        self.merge_sources = merge_sources or []

    def as_list(self) -> list[Event]:

        events = [event for source in self.sources for event in source.as_list()]
        merge_events = [
            event for source in self.merge_sources for event in source.as_list()
        ]

        if len(merge_events) > 0:
            # we generally assume that either osh was running or not
            # so merging in 'merge_events' is only necessary
            # in those posix timestamps (floored to seconds)
            # that can't be found in 'events'
            events_seconds = {
                math.floor(event.timestamp.timestamp()) for event in events
            }
            # TODO some easy stats here to see if there is much after coming in, or restrict to last 10 days
            # but how to warn, or return stats, or handle it with user notification?
            # print(f"{min(events_seconds)=}")
            # print(f"{len(merge_events)=}")
            merge_events = [
                event
                for event in merge_events
                if math.floor(event.timestamp.timestamp()) not in events_seconds
            ]
            # print(f"{len(merge_events)=}")
            # merge_events_after = [event for event in merge_events if math.floor(event.timestamp.timestamp())>min(events_seconds)]
            # print(f"{len(merge_events_after)=}")

        return events + merge_events


default_file = Path("~/.one-shell-history/history.json")

""" osh file format
a single json:
{
    "format": "osh-history-v1",
    "machine": "...",
    "description": "...",
    events: [
        {
            "timestamp": "...",
            "command": "...",
            "duration": ...,
            "exit-code": ...,
            "folder": "...",
            "session": "...",
        },
        ...
    ],
}
"""


class OshSource(Source):
    def __init__(self, file: Path = default_file):
        self.file = file

    def as_list(self) -> list[Event]:

        file = self.file.expanduser()
        data = json.loads(file.read_text())

        # TODO a nicer way to have a schema and validate?
        assert data["format"] == "osh-history-v1"

        machine = data["machine"]
        assert machine is not None

        return [
            Event(
                timestamp=datetime.datetime.fromisoformat(e["timestamp"]),
                command=e["command"],
                duration=e["duration"],
                exit_code=e["exit-code"],
                folder=e["folder"],
                machine=machine,
                session=e["session"],
            )
            for e in data["events"]
        ]


default_legacy_file = Path("~/.one-shell-history/events.json")


class OshLegacySource(Source):
    def __init__(self, file: Path = default_legacy_file, skip_imported: bool = True):
        self.file = file
        self.skip_imported = skip_imported

    def as_list(self) -> list[Event]:

        file = self.file.expanduser()
        data = json.loads(file.read_text())

        # NOTE the legacy (pre-release) data contains events:
        # 1) imported from zsh -> usually skip
        # 2) osh events with time resolution in seconds -> usually see it from timestamp
        # 3) osh events with time resolution in microseconds -> usually see it from timestamp

        # TODO Event.from_json_dict will go away, then need to do it here explicitely for the future
        events = [Event.from_json_dict(event) for event in data]

        if self.skip_imported:
            events = [e for e in events if e.session is not None]

        return events


class CannotParse(Exception):
    pass


class ZshSource(Source):
    def __init__(
        self,
        file: Path = Path("~/.zsh_history"),
        machine: Optional[str] = None,
    ):
        self.file = file
        self.machine = machine

    def as_list(self) -> list[Event]:

        # TODO i'm not sure if all zsh history are the format as below, or does it depend on zsh settings?
        # maybe check what it looks like on a fresh system
        # and/or see that we fail if not as expected

        pattern = re.compile(
            r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$"
        )

        events = []
        zsh_history = enumerate(
            self.file.expanduser()
            .read_text(encoding="utf-8", errors="replace")
            .split("\n")[:-1],
            start=1,
        )

        for line, content in zsh_history:
            match = pattern.fullmatch(content)
            if match is None:
                raise CannotParse(f"cannot parse line {line} = {content.strip()}")
            # from what I understand, zsh_history uses a posix time stamp, utc, second resolution
            timestamp = datetime.datetime.fromtimestamp(
                int(match["timestamp"]), tz=datetime.timezone.utc
            )
            command = match["command"]
            # note: duration in my zsh version 5.8 doesnt seem to be recorded correctly, its always 0
            # duration = int(match.group("duration"))
            while command.endswith("\\"):
                line, content = next(zsh_history)
                command = command[:-1] + "\n" + content
            event = Event(timestamp=timestamp, command=command, machine=self.machine)

            events.append(event)

        return events


if __name__ == "__main__":
    sources = [OshLegacySource()]
    merge_sources = [ZshSource()]
    source = Union(sources, merge_sources)
    events = source.as_list()
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
