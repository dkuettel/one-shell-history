import json
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from subprocess import PIPE, Popen
from threading import Thread
from typing import Optional, assert_never

import msgspec
import zmq
from typer import Abort, Exit, Typer

# TODO maybe minimize the imports for speed
# run the imports only in the commands, or in other files later


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
    # TODO not so easy to read it reverse ... unless we make a file format that supports that well? (utf8 is painful here)
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
class RequestPreview:
    index: int


@dataclass(frozen=True)
class RequestEvents:
    start: int
    count: int


@dataclass(frozen=True)
class ReplyAck:
    pass


@dataclass(frozen=True)
class ReplyPreview:
    content: str


@dataclass(frozen=True)
class ReplyEvents:
    events: list[str]


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


def send_exit_to_preview():
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect("ipc://@preview")
    socket.send(b"exit")
    assert socket.recv() == b"ack"
    socket.close()
    context.destroy()


@contextmanager
def fzf_running(query: str):
    with Popen(
        [
            # NOTE checked docs up to 0.55
            "fzf",
            "--height=70%",
            "--min-height=10",
            "--header=some-header",
            f"--query={query}",
            "--tiebreak=index",
            "--scheme=history",
            # "--tac",  # TODO reversed, could we then not sort? but it means we add the most relevant last?
            "--read0",
            "--info=inline-right",
            "--highlight-line",
            "--delimiter=\x1f",
            # NOTE --with-nth is applied first, then --nth is relative to that
            "--with-nth=2..",  # what to show
            "--nth=2..",  # what to search
            "--preview-window=down:10:wrap",
            "--preview=python -m draft get-preview {1}",
            "--print0",
            "--print-query",
            # TODO how to manage switching modes? simple restart, or reload?
            "--expect=enter,esc,ctrl-c,tab,shift-tab",
        ],
        text=True,
        stdin=PIPE,
        stdout=PIPE,
    ) as p:
        assert p.stdin is not None
        assert p.stdout is not None
        yield p.stdin, p.stdout, p.wait


@contextmanager
def thread(target: Callable[[], None]):
    thread = Thread(target=target)
    thread.start()
    try:
        yield
    finally:
        thread.join()


@dataclass
class Server:
    events: list[Event]

    @classmethod
    def from_path(cls, base: Path = Path("test-data")):
        # TODO just for testing, not incremental yet, not backgrounded
        events = load_history(base, Order.recent_first)
        # events, indexed = index_history(events)
        events = list(events)
        return cls(events)

    def run(self):
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
                        socket.send_pyobj(ReplyAck())
                        # TODO what about the loading thread(s)? it might not be finished yet? can we interrupt it?
                        return

                    case RequestPreview(index):
                        if index >= len(self.events):
                            socket.send(b"... loading ...")
                            continue
                        event = self.events[index]
                        socket.send_pyobj(ReplyPreview(preview_from_event(event, tz)))

                    case RequestEvents(start, count):
                        now = datetime.now(timezone.utc)
                        socket.send_pyobj(
                            ReplyEvents(
                                [
                                    fzf_entry_from_event(i, event, now)
                                    for i, event in enumerate(
                                        self.events[start : start + count], start=start
                                    )
                                ]
                            )
                        )

                    case _ as never:
                        assert_never(never)


@contextmanager
def running_server(server: Server):
    with thread(server.run):
        yield server


app = Typer(pretty_exceptions_enable=False)


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
def get_preview(index: int):
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect("ipc://@preview")
    socket.send(str(index).encode())
    message = socket.recv()
    print(message.decode())


@app.command()
def quick(query: str = ""):
    with Popen(
        [
            # NOTE checked docs up to 0.55
            "fzf",
            "--height=70%",
            "--min-height=10",
            "--header=some-header",
            f"--query={query}",
            "--tiebreak=index",
            "--scheme=history",
            # "--tac",  # TODO reversed, could we then not sort? but it means we add the most relevant last?
            "--read0",
            "--info=inline-right",
            "--highlight-line",
            "--delimiter=\x1f",
            # NOTE --with-nth is applied first, then --nth is relative to that
            "--with-nth=2..",  # what to show
            "--nth=2..",  # what to search
            "--preview-window=down:10:wrap",
            "--preview=python -m draft preview {1}",
            "--print0",
            "--print-query",
            # TODO how to manage switching modes? simple restart, or reload?
            "--expect=enter,esc,ctrl-c,tab,shift-tab",
            "--bind=start:reload:python -m draft events",
        ],
        text=True,
        stdout=PIPE,
    ) as p:
        assert p.stdout is not None

        # TODO how far can we go with threads? we want to be responsive, but also need to load the data
        # TODO make this into a nice tuple context when above is shorter/abstracted
        server = Server.from_path()
        with running_server(server):
            result = p.stdout.read().split("\x00")
            exit_code = p.wait()
            exit()

    if exit_code != 0:
        raise Exit(exit_code)

    match result:
        case [str() as query, str() as key, str() as selection, ""]:
            # eg, ['draft', 'enter', '5\x1f[ 1y ago] \x1ftime python -m draft > /dev/null', '']
            match key:
                case "enter":
                    # TODO lets use unique indices, not just int for reverse search, opaque string into a full general-purpose dict?
                    # since the main thing is in a thread here, we should have access?
                    index = int(selection.split("\x1f", maxsplit=1)[0])
                    print(server.events[index].command)
                case _:
                    print(
                        f"fzf returned with an unexpected key: {key}",
                        file=sys.stderr,
                    )
                    raise Abort()
        case _:
            print(f"fzf returned unexpected data: {result}", file=sys.stderr)
            raise Abort()


@app.command()
def serve():
    with running_server(Server.from_path()):
        pass


def fzf_entry_from_event(i: int, event: Event, now: datetime) -> str:
    ago = human_duration(now - event.timestamp)
    cmd = event.command.replace("\n", "")
    return f"{i}\x1f[{ago: >3} ago] \x1f{cmd}"


@app.command()
def exit():
    with request_socket() as socket:
        socket.send_pyobj(RequestExit())
        assert socket.recv_pyobj() == ReplyAck()


@app.command()
def preview(index: int):
    with request_socket() as socket:
        socket.send_pyobj(RequestPreview(index))
        match socket.recv_pyobj():
            case ReplyPreview(content):
                print(content)
            case _ as never:
                assert_never(never)


@app.command()
def events():
    # TODO not supporting session yet, and also modes and all that, aggregation, mode state is kept by the server?
    # query = "test"
    # session = "2e715f13-1248-443f-ae0f-65d315ae9b18"
    with request_socket() as socket:
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
