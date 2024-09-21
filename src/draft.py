from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from threading import Thread
from typing import Optional, assert_never

import msgspec
import zmq
from typer import Exit, Typer


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
    event: Optional[Event] = None


def load_osh_history(base: Path) -> Iterator[Event]:
    sources = base.rglob("*.osh")
    decoder = msgspec.json.Decoder(type=Entry)
    # for source in sources:
    #     for i in decoder.decode_lines(source.read_text()):
    #         if i.event is not None:
    #             yield i.event
    # TODO not so easy to read it reverse ... unless we make a file format that supports that well? (utf8 is painful here, fixed lengths?)
    # or we could remember what we read last time, and if the last content is still the same, we know where to continue, that should be very little
    # (but still would not be backwards)
    for source in sources:
        with source.open("rt") as f:
            for l in f:
                if len(l) > 0:
                    match decoder.decode(l).event:
                        case Event() as e:
                            yield e


event_pattern = re.compile(r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$")


def read_zsh_file(file: Path):
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


def load_zsh(base: Path):
    sources = base.rglob("*.zsh_history")
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


def load_legacy(base: Path):
    sources = base.rglob("*.osh_legacy")
    events = [event for source in sources for event in read_osh_legacy_file(source)]
    return events


class Order(Enum):
    # TODO shaky to use the values we need for sorted :)
    recent_first = True
    oldest_first = False


def load_history(base: Path, order: Order) -> Iterator[Event]:
    # cache = base / "cache.pickle"
    # if cache.exists():
    #     return pickle.loads(cache.read_bytes())
    # TODO eventually try threads or processes per file? not per file type, and incremental for early start?
    events = load_osh_history(base)  # + load_zsh(base) + load_legacy(base)
    events = sorted(events, key=lambda e: e.timestamp, reverse=order.value)
    # cache.write_bytes(pickle.dumps(events))
    return iter(events)


def index_history(
    events: Iterator[Event],
) -> tuple[Iterator[tuple[int, Event]], list[Event]]:
    indexed: list[Event] = []

    def g() -> Iterator[tuple[int, Event]]:
        for i, event in enumerate(events):
            indexed.append(event)
            yield i, event

    return g(), indexed


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


@dataclass(frozen=True)
class RequestExit:
    pass


@dataclass(frozen=True)
class ReplyExit:
    pass


@dataclass(frozen=True)
class RequestPreview:
    index: int


@dataclass(frozen=True)
class ReplyPreview:
    content: str


@dataclass(frozen=True)
class RequestEvents:
    start: int
    count: int


@dataclass(frozen=True)
class ReplyEvents:
    events: list[str]


@dataclass(frozen=True)
class RequestResult:
    index: int


@dataclass(frozen=True)
class ReplyResult:
    content: str


@dataclass(frozen=True)
class RequestMode:
    change: ModeChange | None


@dataclass(frozen=True)
class ReplyMode:
    mode: Mode


@contextmanager
def request_socket():
    with (
        zmq.Context() as context,
        context.socket(zmq.REQ) as socket,
    ):
        socket.connect("ipc://@server")
        yield socket


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


@contextmanager
def thread(target: Callable[[], None]):
    thread = Thread(target=target)
    thread.start()
    try:
        yield
    finally:
        thread.join()


class Mode(Enum):
    reverse = "reverse"
    session = "session"


class ModeChange(Enum):
    next = "next"
    previous = "previous"


def run_server(base: Path, session: str | None, mode: Mode):
    # TODO not supporting session yet, and also modes and all that, aggregation, mode state is kept by the server?
    # TODO just for testing, not incremental yet, not backgrounded
    events = load_history(base, Order.recent_first)
    # events, indexed = index_history(events)
    events = list(events)

    if session is not None:
        session_events = [e for e in events if e.session == session]
    else:
        session_events = events  # TODO just disable session mode in this case

    tz = datetime.now().astimezone().tzinfo
    assert tz is not None

    with (
        zmq.Context() as context,
        context.socket(zmq.REP) as socket,
    ):
        socket.bind("ipc://@server")

        while True:
            match socket.recv_pyobj():
                case RequestExit():
                    socket.send_pyobj(ReplyExit())
                    # TODO what about the loading thread(s)? it might not be finished yet? can we interrupt it?
                    return

                case RequestPreview(index):
                    if index >= len(events):
                        socket.send(b"... loading ...")
                        continue
                    event = events[index]
                    socket.send_pyobj(ReplyPreview(preview_from_event(event, tz)))

                case RequestEvents(start, count):
                    now = datetime.now(timezone.utc)
                    match mode:
                        case Mode.reverse:
                            socket.send_pyobj(
                                ReplyEvents(
                                    [
                                        fzf_entry_from_event(i, event, now)
                                        for i, event in enumerate(
                                            events[start : start + count], start=start
                                        )
                                    ]
                                )
                            )
                        case Mode.session:
                            socket.send_pyobj(
                                ReplyEvents(
                                    [
                                        fzf_entry_from_event(i, event, now)
                                        for i, event in enumerate(
                                            session_events[start : start + count],
                                            start=start,
                                        )
                                    ]
                                )
                            )
                        case _ as never:
                            assert_never(never)

                case RequestResult(index):
                    event = events[index]
                    socket.send_pyobj(ReplyResult(event.command))

                case RequestMode(change):
                    if change is not None:
                        mode = change_mode(mode, change)
                    socket.send_pyobj(ReplyMode(mode))

                case _ as never:
                    assert_never(never)


def change_mode(mode: Mode, change: ModeChange) -> Mode:
    modes = list(Mode)
    i = modes.index(mode)
    match change:
        case ModeChange.next:
            return modes[(i + 1) % len(modes)]
        case ModeChange.previous:
            return modes[(i - 1) % len(modes)]
        case _ as never:
            assert_never(never)


def fzf_entry_from_event(i: int, event: Event, now: datetime) -> str:
    ago = human_duration(now - event.timestamp)
    cmd = event.command.replace("\n", "")
    return f"{i}\x1f[{ago: >3} ago] \x1f{cmd}"


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


# TODO typer import seems to be the majority of startup time ... click is actually quite a bit faster
# TODO consider heapq.merge?


@app.command()
def serve(session: str | None = None, mode: Mode | None = None):
    # TODO we could also get session and session start from the env?
    if mode is None:
        mode = Mode.reverse
    run_server(base=Path("test-data"), session=session, mode=mode)


@app.command()
def exit(index: int | None = None, fail: bool = False):
    with request_socket() as socket:
        match index:
            case int():
                socket.send_pyobj(RequestResult(index))
                match socket.recv_pyobj():
                    case ReplyResult(content):
                        print(content)
                    case _ as never:
                        assert_never(never)
            case None:
                pass
            case _ as never:
                assert_never(never)

        socket.send_pyobj(RequestExit())
        assert socket.recv_pyobj() == ReplyExit()

    if fail:
        raise Exit(1)


@app.command()
def get_preview(index: int):
    with request_socket() as socket:
        socket.send_pyobj(RequestPreview(index))
        match socket.recv_pyobj():
            case ReplyPreview(content):
                print(content)
            case _ as never:
                assert_never(never)


@app.command()
def list_events(mode: ModeChange | None = None):
    with request_socket() as socket:
        socket.send_pyobj(RequestMode(mode))
        match socket.recv_pyobj():
            case ReplyMode(m):
                print(f"\x1f{m}", end="\x00")
            case _ as never:
                assert_never(never)

        start, batch = 0, 1000
        while True:
            socket.send_pyobj(RequestEvents(start, batch))
            match socket.recv_pyobj():
                case ReplyEvents([]):
                    break
                case ReplyEvents(events):
                    for event in events:
                        print(event, end="\x00")
                case _ as never:
                    assert_never(never)
            start += batch


if __name__ == "__main__":
    app()
