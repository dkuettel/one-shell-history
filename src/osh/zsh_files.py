import datetime
import json
import re
from pathlib import Path

from osh.history import Event


class CannotParse(Exception):
    pass


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
            raise CannotParse(
                f"cannot parse around {file}:{line} = {json.dumps(content)}"
            )
        # from what I understand, zsh_history uses a posix time stamp, utc, second resolution (floor of float seconds)
        timestamp = datetime.datetime.fromtimestamp(
            int(match["timestamp"]), tz=datetime.timezone.utc
        )
        command = match["command"]
        # note: duration in my zsh version 5.8 doesnt seem to be recorded correctly, its always 0
        # duration = int(match.group("duration"))
        while command.endswith("\\"):
            line, content = next(zsh_history)
            command = command[:-1] + "\n" + content
        event = Event(timestamp=timestamp, command=command)

        events.append(event)

    return events
