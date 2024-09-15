import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from subprocess import PIPE, Popen
from threading import Thread
from typing import Optional, TextIO, assert_never

import msgspec
import zmq
from typer import Typer


class Event(msgspec.Struct, frozen=True):
    timestamp: datetime
    command: str
    duration: float | None = None
    exit_code: int | None = None
    folder: str | None = None
    machine: str | None = None
    session: str | None = None


# TODO eventually we have to deal with a union, because there is more than one type of entry?
# we dont actually now respect any change in format, maybe just dont support that anymore
# make the extension, or the first line define the format, and that's it
class Entry(msgspec.Struct):
    event: Optional[Event] = None


def load_osh(base: Path):
    sources = base.rglob("*.osh")
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


def load_simple(base: Path, order: Order) -> list[Event]:
    # TODO eventually try threads or processes per file? not per file type
    events = load_osh(base) + load_zsh(base) + load_legacy(base)
    events = sorted(events, key=lambda e: e.timestamp, reverse=order.value)
    return events


@dataclass
class History:
    events: list[Event] | None

    @classmethod
    def from_empty(cls):
        return cls(events=None)


def human_ago(dt: timedelta) -> str:
    s = dt.total_seconds()
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


def write_backwards(history: History, out: TextIO):
    base = Path("test-data")
    history.events = load_simple(base, Order.recent_first)
    now = datetime.now(timezone.utc)
    try:
        for i, e in enumerate(history.events):
            ago = human_ago(now - e.timestamp)
            cmd = e.command.replace("\n", "")
            out.write(f"{i}\x1f[{ago: >3} ago] {cmd}\x00")
        out.close()
    except BrokenPipeError:
        pass  # NOTE thats when fzf exits before we finish


@dataclass
class Previews:
    history: History

    @contextmanager
    def while_serving(self):
        thread = Thread(target=self.serve)
        thread.start()
        try:
            yield
        finally:
            self.send_exit()
            thread.join()

    def serve(self):
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind("ipc://@preview")

        while True:
            match socket.recv():
                case b"exit":
                    socket.send(b"ack")
                    break
                case bytes() as message:
                    if self.history.events is None:
                        socket.send(b"... loading ...")
                        continue
                    index = int(message.decode())
                    event = self.history.events[index]
                    # TODO time should be local time, not utc
                    socket.send(f"{event.timestamp}\n{event.command}".encode())
                case _ as never:
                    assert_never(never)

        socket.close()
        context.destroy()

    def send_exit(self):
        context = zmq.Context()
        socket = context.socket(zmq.REQ)
        socket.connect("ipc://@preview")
        socket.send(b"exit")
        assert socket.recv() == b"ack"
        socket.close()
        context.destroy()


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
def list_backwards():
    history = History.from_empty()

    with Previews(history).while_serving():
        with Popen(
            [
                "fzf",
                "--height=70%",
                "--min-height=10",
                "--header=some-header",
                # "--query=something",
                "--tiebreak=index",
                "--read0",
                "--delimiter=\x1f",
                "--with-nth=2..",  # TODO what do display make different from what to search?
                # TODO check nth vs with-nth again
                "--preview-window=down:10:wrap",
                "--preview=python -m draft get-preview {1}",
                "--print0",
                "--print-query",
                "--expect=enter",
            ],
            text=True,
            stdin=PIPE,
            stdout=PIPE,
        ) as p:
            assert p.stdin is not None
            assert p.stdout is not None

            write_thread = Thread(
                target=write_backwards,
                args=(
                    history,
                    p.stdin,
                ),
            )
            write_thread.start()

            print(p.stdout.read().split("\x00"))
            print(p.wait())

            write_thread.join()


if __name__ == "__main__":
    app()
