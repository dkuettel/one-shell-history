from __future__ import annotations

import json
import re
from base64 import b64encode
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from typing import assert_never

import msgspec
from typer import Typer


# TODO discuss with yves a new format?
class Event(msgspec.Struct, frozen=True):
    timestamp: datetime
    command: str
    # TODO the typing doesnt show that either all of them are missing, or none, and in the future none are missing?
    duration: float | None = None
    exit_code: int | None = msgspec.field(name="exit-code", default=None)
    folder: str | None = None
    machine: str | None = None
    session: str | None = None


# TODO eventually we have to deal with a union, because there is more than one type of entry?
# we dont actually now respect any change in format, maybe just dont support that anymore
# make the extension, or the first line define the format, and that's it
class Entry(msgspec.Struct):
    event: Event | None = None


def load_osh_histories(base: Path) -> list[Event]:
    sources = base.rglob("*.osh")
    decoder = msgspec.json.Decoder(type=Entry)
    return [
        i.event
        for source in sources
        for i in decoder.decode_lines(source.read_text())
        if i.event is not None
    ]


event_pattern = re.compile(r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$")


def read_zsh_file(file: Path) -> list[Event]:
    # TODO i'm not sure if all zsh history are the format as below, or does it depend on zsh settings?
    # maybe check what it looks like on a fresh system
    # and/or see that we fail if not as expected

    # TODO no expanduser anymore
    # in a way that should be gone after the zsh/python boundary
    # after all, how then would you ever have a file with actual ~ in it?
    # ok but it only does ~ at the beginning, so its not that bad
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


def load_zsh(base: Path) -> list[Event]:
    sources = base.rglob("*.zsh_history")
    events = [event for source in sources for event in read_zsh_file(source)]
    return events


def read_osh_legacy_file(file: Path, skip_imported: bool = True) -> list[Event]:
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


def load_legacy(base: Path) -> list[Event]:
    sources = base.rglob("*.osh_legacy")
    events = [event for source in sources for event in read_osh_legacy_file(source)]
    return events


def load_history(base: Path) -> list[Event]:
    """history is from new to old, first entry is the most recent"""
    events = load_osh_histories(base) + load_zsh(base) + load_legacy(base)
    events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # TODO msgspec is actually super fast, json is already good compared to pickle
    # and msgpack seems even a bit faster
    # so we could make the source format already this way, and we could cache things
    # we have a class cache state, and maybe even know where to continue reading active files, if needed

    return events


def human_duration(dt: timedelta | float) -> str:
    match dt:
        case timedelta():
            ms = dt.total_seconds() * 1000
        case float() | int():
            ms = dt * 1000
        case _ as never:
            assert_never(never)

    if ms < 1000:
        return f"{round(ms)}ms"
    s = ms / 1000
    if s < 60:
        return f"{round(s)}s"
    m = s / 60
    if m < 60:
        return f"{round(m)}m"
    h = m / 60
    if h < 24:
        return f"{round(h)}h"
    d = h / 24
    if d < 7:
        return f"{round(d)}d"
    if d < 365:
        return f"{round(d / 7)}w"
    y = d / 365
    return f"{round(y)}y"


def preview_from_event(event: Event, tz: tzinfo) -> str:
    ts = event.timestamp.astimezone(tz)
    match event.folder, event.machine, event.exit_code, event.duration:
        case str() as folder, str() as machine, int() as exit_code, float() as duration:
            dt = human_duration(duration)
            parts = [
                f"[returned {exit_code} after {dt} on {ts}]",
                # TODO replace ~ again?
                f"[ran in {folder} on {machine}]",
                "",
                event.command,
            ]
        case _:
            parts = [
                f"ran on {ts}",
                "",
                event.command,
            ]
    return "\n".join(parts)


def entry_from_event(event: Event, now: datetime, tz: tzinfo) -> str:
    enc_cmd = b64encode(event.command.encode()).decode()
    enc_preview = b64encode(preview_from_event(event, tz).encode()).decode()
    ago = human_duration(now - event.timestamp)
    # TODO for safety remove/replace all fzf_*, especially fzf_end
    cmd = event.command.replace("\n", "")
    return "\x1f".join(
        [
            enc_cmd,
            enc_preview,
            f"[{ago: >3} ago] ",
            cmd,
        ]
    )


class Mode(Enum):
    all = "all"
    session = "session"
    folder = "folder"


app = Typer(pretty_exceptions_enable=False)


# TODO try for aggregation
@app.command()
def frames():
    import pandas

    base = Path("test-large")
    [source] = list(base.rglob("*.osh"))
    df = pandas.read_json(source, lines=True)
    print(df)

    # NOTE this seems definitely slower
    # but for certain things dataframes are nice, aggregation
    # so we might still use it, but not for loading?
    # unless we make the format better for pandas?
    # speed was actually faster when loading with msgspec and then passing to dataframes
    # but need to properly unpack into columns


@app.command()
def search(
    mode: Mode | None = None,
    session: str | None = None,
    folder: str | None = None,
):
    if mode is None:
        mode = Mode.all

    events = load_history(Path("test-data"))

    match mode:
        case Mode.all:
            pass
        case Mode.session:
            if session is not None:
                events = [e for e in events if e.session == session]
        case Mode.folder:
            if folder is not None:
                events = [e for e in events if e.folder == folder]
        case _ as never:
            assert_never(never)

    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None

    for event in events:
        print(entry_from_event(event, now, local_tz), end="\x00")


if __name__ == "__main__":
    app()
