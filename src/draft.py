import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from subprocess import PIPE, Popen, run
from threading import Thread
from typing import Optional

import msgspec
import zmq
from typer import Typer


# TODO it worked without giving order? ah no we do it ourselves
# but if we make timestamp the first, and give order, would that make it faster and more native?
# there is order=True and frozen=True
class Event(msgspec.Struct):
    timestamp: datetime
    command: str


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


def load_simple(base: Path):
    # TODO eventually try threads or processes per file? not per file type
    events = load_osh(base) + load_zsh(base) + load_legacy(base)
    # TODO we could assume that parts are already sorted, that could make it faster
    events = sorted(events, key=lambda e: e.timestamp)
    return events


# NOTE didnt seem to add timings
app = Typer()


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
    events = None

    def g(out):
        nonlocal events
        base = Path("test-data")
        # TODO does that cost time? listing the reversed? make the sort reversed to start with? an in-place?
        events = list(reversed(load_simple(base)))
        count = len(events)
        width = len(str(count))
        for i, e in enumerate(events):
            # TODO the full width for reverse index looks stupid
            # TODO we also want to add the xyz ago in a very condensed manner?
            out.write(f"{i: {width}d}" + "\x1f " + e.command + "\x00")
        # TODO shouldnt fail on "broken pipe" or similar, when fzf exits early
        out.close()

    def p():
        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.bind("ipc://@preview")
        # TODO need to exit eventually, per message? or signal while socket.recv()?
        while True:
            message = socket.recv()
            if message == b"exit":
                print("exit received")
                socket.send(b"ack")
                break
            if events is None:
                socket.send(b"... loading ...")
                continue
            i = int(message.decode())
            e = events[i]
            socket.send(f"{e.timestamp}\n{e.command}".encode())
        socket.close()
        context.destroy()
        print("received exit done")

    pthread = Thread(target=p)
    pthread.start()

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
            # "--with-nth=2..",  # TODO what do display make different from what to search?
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
        thread = Thread(target=g, args=(p.stdin,))
        thread.start()
        # TODO should join on it?
        print(p.stdout.read(None).split("\x00"))
        print(p.wait())

    # TODO stop the preview thread, need to use same context?
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect("ipc://@preview")
    socket.send(b"exit")
    print("exit requested")
    socket.recv()


if __name__ == "__main__":
    app()
