import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import msgspec


class Event(msgspec.Struct):
    timestamp: datetime
    command: str


# TODO eventually we have to deal with a union, because there is more than one type of entry?
# we dont actually now respect any change in format, maybe just dont support that anymore
# make the extension, or the first line define the format, and that's it
class Entry(msgspec.Struct):
    event: Optional[Event] = None


def load_osh():
    sources = Path("test-data").rglob("*.osh")
    decoder = msgspec.json.Decoder(type=Entry)
    events = [
        i.event
        for source in sources
        for i in decoder.decode_lines(source.read_text())
        if i.event is not None
    ]
    return events


event_pattern = re.compile(r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$")


def read_zsh_file(file: Path):
    # TODO i'm not sure if all zsh history are the format as below, or does it depend on zsh settings?
    # maybe check what it looks like on a fresh system
    # and/or see that we fail if not as expected

    # TODO no expanduser anymore
    file = file.expanduser()

    events = []
    zsh_history = enumerate(
        file.read_text(encoding="utf-8", errors="replace").split("\n")[:-1],
        start=1,
    )

    for line, content in zsh_history:
        match = event_pattern.fullmatch(content)
        if match is None:
            assert False, (file, line, json.dumps(content))
        # from what I understand, zsh_history uses a posix time stamp, utc, second resolution (floor of float seconds)
        timestamp = datetime.fromtimestamp(int(match["timestamp"]), tz=timezone.utc)
        command = match["command"]
        # note: duration in my zsh version 5.8 doesnt seem to be recorded correctly, its always 0
        # duration = int(match.group("duration"))
        while command.endswith("\\"):
            line, content = next(zsh_history)
            command = command[:-1] + "\n" + content
        # TODO currently using the "new" Event class, works coincidentally
        event = Event(timestamp=timestamp, command=command)

        events.append(event)

    return events


def load_zsh():
    sources = Path("test-data").rglob("*.zsh_history")
    events = [event for source in sources for event in read_zsh_file(source)]
    return events


def main():
    events = load_osh() + load_zsh()
    # TODO we could assume that parts are already sorted, that could make it faster
    events = sorted(events, key=lambda e: e.timestamp)
    for e in events:
        print(e.command)


if __name__ == "__main__":
    main()
