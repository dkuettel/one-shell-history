from __future__ import annotations

import json
import multiprocessing as mp
import os
import re
import time
from base64 import b64encode
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from queue import SimpleQueue
from threading import Thread
from typing import Literal, assert_never

import msgspec
from typer import Typer


# TODO discuss with yves a new format?
class Event(msgspec.Struct, frozen=True):
    timestamp: datetime
    command: str
    # TODO the typing doesnt show that either all of them are missing, or none, and in the future none are missing?
    # TODO or we use a union top level
    duration: float | None = None
    exit_code: int | None = msgspec.field(name="exit-code", default=None)
    folder: str | None = None
    machine: str | None = None
    session: str | None = None


# TODO eventually we have to deal with a union, because there is more than one type of entry?
# we dont actually now respect any change in format, maybe just dont support that anymore
# make the extension, or the first line define the format, and that's it
# or the top level entry is the thing the gives the version info, and we add to that in the future?
# the v1 top level thing is very good to make it safe for later additions, and easy to parse
# but how to make it robust to the higher level streaming thing?
# i want to see that we can read it backwards maybe? or save parts already backwards? maybe not that one.
# could we literally reverse the bytes? what does msgpack do when it gets too much data, just stop?
# otherwise we save 2 messages, the real one, and a size? and then we can read backwards?
# not quite, if the size is a message to, how can we read that one backwards, would have to be my own int thing
# but then that is a fixed top-level format that we cannot just change now, and we need a new fileformat if we want to change that
class Entry(msgspec.Struct):
    event: Event | None = None


class NewEvent(msgspec.Struct, frozen=True):
    timestamp: datetime
    command: str
    duration: float
    exit_code: int
    folder: str
    machine: str
    session: str


# TODO should not forget that we need to look at locking when multiple appends
# and even more so when adapting/compacting when loading


# TODO and we try to make sequences of binary msgpack entries, lets try if we can load them streaming, or if we need to wrap things somehow?
# streaming is not supported by msgspec, the messages are not self-delimiting
# so we need to built it one level higher, the question here is if my data has a size that will make this slower?
# do we want to easy append, or easy load?
# or maybe if we are lucky, then a list is just appending?
# no :/, a list seems to know its length, so we cant cheat it
# the data seems so small that maybe a single message as a list is good enough and we benefit from native code?
# will still be fun to try the reverse streaming on as well
class NewEntry(msgspec.Struct, frozen=True):
    v1: NewEvent


def load_osh_histories(base: Path) -> list[Event]:
    sources = base.rglob("*.osh")
    decoder = msgspec.json.Decoder(type=Entry)
    return [
        i.event
        for source in sources
        for i in decoder.decode_lines(source.read_text())
        if i.event is not None
    ]


class OshMsgpack(msgspec.Struct, frozen=True, tag_field="version", tag="v1"):
    events: list[Event]


entry_decoder = msgspec.json.Decoder(type=Entry)
msgpack_decoder = msgspec.msgpack.Decoder(type=OshMsgpack)
msgpack_stream_decoder = msgspec.msgpack.Decoder(type=Event)


def read_osh_file(path: Path) -> Iterator[Event]:
    for i in entry_decoder.decode_lines(path.read_text()):
        if i.event is not None:
            yield i.event


def read_msgpack_file(path: Path) -> Iterator[Event]:
    yield from reversed(msgpack_decoder.decode(path.read_bytes()).events)


def write_msgpack_file(events: list[Event], path: Path):
    path.write_bytes(msgspec.msgpack.encode(OshMsgpack(events)))


def write_msgpack_stream_file(events: list[Event], path: Path):
    # TODO we assume events are most recent first, and we reverse it, so it's ready to be read and appended to
    events = list(reversed(events))
    # TODO how about versioning then, we need a tag here too?
    encoder = msgspec.msgpack.Encoder()
    with path.open("wb") as f:
        for event in events:
            data = encoder.encode(event)
            size = len(data)
            f.write(data)
            f.write(size.to_bytes(length=2, byteorder="big", signed=False))


def read_msgpack_stream_file(path: Path) -> Iterator[Event]:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        while f.tell() > 0:
            f.seek(-2, os.SEEK_CUR)
            size = int.from_bytes(f.read(2), byteorder="big", signed=False)
            f.seek(-2 - size, os.SEEK_CUR)
            yield msgpack_stream_decoder.decode(f.read(size))
            f.seek(-size, os.SEEK_CUR)


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


def find_sources(base: Path) -> set[Path]:
    sources = {
        *base.rglob("*.osh_legacy"),
        *base.rglob("*.zsh_history"),
        *base.rglob("*.osh"),
    }

    def f(source: Path) -> Path:
        alt = source.with_suffix(source.suffix + ".msgpack")
        if alt.exists():
            return alt
        return source

    sources = {f(source) for source in sources}
    return sources


def load_source(path: Path) -> Iterator[Event]:
    match path.suffixes:
        case [".osh_legacy"]:
            # TODO but is that gonna be reverse? not yet
            yield from read_osh_legacy_file(path)
        case [".zsh_history"]:
            # TODO but is that gonna be reverse? not yet
            yield from read_zsh_file(path)
        case [".osh"]:
            # TODO but is that gonna be reverse? not yet
            yield from read_osh_file(path)
        case [".osh", ".msgpack"]:
            yield from read_msgpack_file(path)
        case _ as never:
            assert False, never


# TODO maybe base should be absolute already
def load_history(base: Path) -> Iterator[Event]:
    active_sources = find_sources(base / "active")
    archived_sources = find_sources(base / "archive")
    # TODO there is a way to list rglob and get stat() at the same time?
    # TODO could still check how easy it is to load reversely now and merge sort? slower overall, but faster time to first result?
    # TODO especially if we can do it in parallel threaded or so, new python abilities to use here?
    active_sources = sorted(
        active_sources,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    # TODO already much faster to first event, but currently cheating, not reverse? maybe only best effort, just all data, not necessarily any reverse
    # TODO in fact we might already be at python's overhead time, removing all but the new format gives the same time, 0.09s, could be fine already
    # haha ok no that was stupid. we only yield, so we never go and open any other file, so it can't make a difference of course
    # TODO now lets rewrite old formats automatically when we encounter them and use the same name, and then skip if there is a newer one with a fitting name?
    for source in active_sources:
        yield from load_source(source)

    archived_mtime = max(path.stat().st_mtime for path in archived_sources)

    cached_source = base / "archived.osh.msgpack"
    if not cached_source.exists() or cached_source.stat().st_mtime < archived_mtime:
        archived = [
            event for source in archived_sources for event in load_source(source)
        ]
        archived = sorted(
            archived,
            key=lambda e: e.timestamp,
            reverse=True,
        )
        write_msgpack_file(archived, cached_source)
    else:
        archived = load_source(cached_source)

    yield from archived

    # events = load_osh_histories(base) + load_zsh(base) + load_legacy(base)
    # TODO on this test data, sorting makes hardly any impact
    # events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # TODO msgspec is actually super fast, json is already good compared to pickle
    # and msgpack seems even a bit faster
    # so we could make the source format already this way, and we could cache things
    # we have a class cache state, and maybe even know where to continue reading active files, if needed


def threaded_worker(path: Path, queue: SimpleQueue[Event | None]):
    for event in load_source(path):
        queue.put(event)
    queue.put(None)


def load_history_threaded(base: Path) -> Iterator[Event]:
    """total is the same, but time to first is slower"""
    sources = find_sources(base)
    queue: SimpleQueue[Event | None] = SimpleQueue()
    threads = [
        Thread(target=threaded_worker, args=(source, queue)) for source in sources
    ]
    for thread in threads:
        thread.start()
    none_count = 0
    while none_count < len(sources):
        match queue.get():
            case Event() as event:
                yield event
            case None:
                none_count = none_count + 1
            case _ as never:
                assert_never(never)
    for thread in threads:
        thread.join()


def process_worker(path: Path, queue: mp.Queue[Event | None]):
    for event in load_source(path):
        queue.put(event)
    queue.put(None)


def load_history_mp(base: Path) -> Iterator[Event]:
    """first almost with no parallel at all, but total much slower. should send in bigger packs?"""
    sources = find_sources(base)
    queue: mp.Queue[Event | None] = mp.Queue()
    processes = [
        mp.Process(target=process_worker, args=(source, queue)) for source in sources
    ]
    for process in processes:
        process.start()
    none_count = 0
    while none_count < len(sources):
        match queue.get():
            case Event() as event:
                yield event
            case None:
                none_count = none_count + 1
            case _ as never:
                assert_never(never)
    for process in processes:
        process.join()


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


def preview_from_event(event: Event | BaggedEvent, tz: tzinfo) -> str:
    ts = event.timestamp.astimezone(tz)
    match event:
        case Event():
            match event.folder, event.machine, event.exit_code, event.duration:
                case (
                    str() as folder,
                    str() as machine,
                    int() as exit_code,
                    float() as duration,
                ):
                    dt = human_duration(duration)
                    parts = [
                        f"[returned {exit_code} after {dt} at {ts}]",
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

        case BaggedEvent():
            parts = [
                f"[ran {event.count:_} times, most recently at {ts}]",
                f"[{round(100*event.success_ratio)}% success, {round(100*event.failure_ratio)}% failure, {round(100*event.unknown_ratio)}% unknown]",
                "",
                event.command,
            ]

        case _ as never:
            assert_never(never)

    return "\n".join(parts)


def entry_from_event(event: Event | BaggedEvent, now: datetime, tz: tzinfo) -> str:
    enc_cmd = b64encode(event.command.encode()).decode()
    enc_preview = b64encode(preview_from_event(event, tz).encode()).decode()
    ago = human_duration(now - event.timestamp)
    cmd = event.command.replace("\n", "")
    return "\x1f".join(
        [
            enc_cmd,
            enc_preview,
            f"[{ago: >3} ago] ",
            # TODO for safety remove/replace all fzf_*, especially fzf_end?
            cmd,
        ]
    )


@dataclass(frozen=True)
class BaggedEvent:
    timestamp: datetime
    command: str
    count: int
    success_ratio: float
    failure_ratio: float
    unknown_ratio: float

    @classmethod
    def from_bag(cls, command: str, bag: Sequence[Event]) -> BaggedEvent:
        count = len(bag)
        success = sum(1 for e in bag if e.exit_code == 0)
        failure = sum(1 for e in bag if e.exit_code is not None and e.exit_code != 0)
        unknown = sum(1 for e in bag if e.exit_code is None)
        return cls(
            timestamp=bag[0].timestamp,
            command=command,
            count=count,
            success_ratio=success / count,
            failure_ratio=failure / count,
            unknown_ratio=unknown / count,
        )


def bagged_events(events: list[Event]) -> list[BaggedEvent]:
    bagged: dict[str, list[Event]] = {}
    for event in events:
        bagged.setdefault(event.command, []).append(event)
    return [BaggedEvent.from_bag(cmd, bag) for cmd, bag in bagged.items()]


class Mode(Enum):
    all = "all"
    session = "session"
    folder = "folder"
    bag = "bag"


app = Typer(pretty_exceptions_enable=False)


@app.command()
def app_search(
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
        case Mode.bag:
            events = bagged_events(list(events))
        case _ as never:
            assert_never(never)

    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None

    for event in events:
        print(entry_from_event(event, now, local_tz), end="\x00")


@app.command("bench")
def app_bench():
    start = time.perf_counter()
    # looks like doing parallel doesnt help much, building objects is maybe the most expensive part?
    # and then anything that has to push that stuff thru a queue has some overhead on that? maybe with sorting it could still help
    events = load_history(Path("test-data"))
    # events = load_history_threaded(Path("test-data"))
    # events = load_history_mp(Path("test-data"))
    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None
    print(len(entry_from_event(next(events), now, local_tz)))
    first = time.perf_counter()
    print(f"first after {(first-start)*1000:_}ms")
    print(sum(len(entry_from_event(event, now, local_tz)) for event in events))
    last = time.perf_counter()
    print(f"rest after {(last-first)*1000:_}ms")


@app.command("nop")
def app_nop():
    pass


@app.command("append-event")
def append_event():
    pass


@app.command("convert")
def app_convert(path: Path):
    match path.suffixes:
        case [".osh", ".msgpack", ".stream"]:
            pass
        case _:
            # TODO well, not clear, maybe just sort anyway?
            events = list(reversed(list(load_source(path))))
            write_msgpack_stream_file(
                events,
                path.with_name(
                    path.name[: -sum(map(len, path.suffixes))] + ".osh.msgpack.stream"
                ),
            )


# TODO bagged stuff is not as good as before, allow filtering for failed and co? order by most recent?
# TODO append event, and maybe new format?
# TODO stats :)

if __name__ == "__main__":
    app()
