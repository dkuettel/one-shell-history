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
    # in a way that should be gone after the zsh/python boundary
    # after all, how then would you ever have a file with actual ~ in it?
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


def read_osh_legacy_file(file: Path, skip_imported: bool = True):
    from osh.history import Event as OshEvent

    # TODO we dont like expanduser
    file = file.expanduser()
    # TODO we could use the faster msgspec lib, but we will probably convert this
    data = json.loads(file.read_text())

    # NOTE the legacy (pre-release) data contains events:
    # 1) imported from zsh -> usually skip
    # 2) osh events with time resolution in seconds -> usually see it from timestamp
    # 3) osh events with time resolution in microseconds -> usually see it from timestamp

    # TODO Event.from_json_dict will go away, then need to do it here explicitely for the future
    events = [OshEvent.from_json_dict(event) for event in data]

    if skip_imported:
        events = [e for e in events if e.session is not None]

    return [Event(e.timestamp, e.command) for e in events]


def load_legacy():
    sources = Path("test-data").rglob("*.osh_legacy")
    events = [event for source in sources for event in read_osh_legacy_file(source)]
    return events


def main():
    events = load_osh() + load_zsh() + load_legacy()
    # TODO we could assume that parts are already sorted, that could make it faster
    events = sorted(events, key=lambda e: e.timestamp)
    for e in events:
        print(e.command)
    # NOTE it's actually better not to use python -u here
    # also we need to not fail when fzf exits before we finish "broken pipe", probably

    # TODO hm ok I get 206k entries with the production version
    # but here I only get 168k, something is missing
    # ah there is another one "osh_legacy" ... convert anyway?
    # overall making archive into one easy loadable thing would be nice in the end


if __name__ == "__main__":
    main()
