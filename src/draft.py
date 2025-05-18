from __future__ import annotations

import json
import mmap
import multiprocessing as mp
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
from typing import assert_never

import msgspec
from typer import Typer


# TODO discuss with yves a new format?
# NOTE using tag fields so we can potentially use unions later and update the data version
class PackedOshEvent(msgspec.Struct, frozen=True, tag_field="version", tag="v1"):
    # TODO could save this as an easy thing, good enough for sorting, and only make smart when needed?
    # TODO or we could sort on the string too? float or big int would be better
    # TODO maybe assert self.timestamp.tzinfo is datetime.timezone.utc ?
    # TODO some have only second resolution here (just like duration)
    timestamp: datetime
    command: str
    duration: None | int | float
    exit_code: None | int
    folder: None | str
    machine: None | str
    session: None | str


# NOTE using tag fields so we can potentially use unions later and update the data version
class BatchedPackedOshEvents(
    msgspec.Struct,
    frozen=True,
    tag_field="version",
    tag="v1",
):
    events: list[PackedOshEvent]


packed_osh_event_decoder = msgspec.msgpack.Decoder(type=PackedOshEvent)
batched_packed_osh_events_decoder = msgspec.msgpack.Decoder(type=BatchedPackedOshEvents)


def write_streamed_packed_osh_events(
    forward_events: Sequence[PackedOshEvent], path: Path
):
    # TODO here there is no benefit giving the type already? but we could just have one ready globally?
    encoder = msgspec.msgpack.Encoder()
    with path.open("wb") as f:
        for event in forward_events:
            data = encoder.encode(event)
            count = len(data)
            f.write(data)
            f.write(count.to_bytes(length=2, byteorder="big", signed=False))


def read_streamed_packed_osh_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    # TODO this is actually quite a bit faster than a normal file seeking implementation
    # maybe that works for everything and fast enough? is a direct total load still faster? probably yes
    with (
        # TODO i dont understand why the plus here
        path.open("r+b") as f,
        mmap.mmap(f.fileno(), 0) as mm,
    ):
        at = len(mm) - 2
        while at > 0:
            count = int.from_bytes(mm[at : at + 2], byteorder="big", signed=False)
            yield packed_osh_event_decoder.decode(mm[at - count : at])
            at = at - count - 2


def write_batched_packed_osh_events(
    forward_events: Sequence[PackedOshEvent], path: Path
):
    path.write_bytes(
        msgspec.msgpack.encode(BatchedPackedOshEvents(list(forward_events)))
    )


def read_batched_packed_osh_events_forward(path: Path) -> Iterator[PackedOshEvent]:
    # TODO mmap might be faster here too? or read_bytes just the same?
    with path.open("rb") as f:
        data = batched_packed_osh_events_decoder.decode(f.read())
        yield from data.events


def read_batched_packed_osh_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    yield from reversed(list(read_batched_packed_osh_events_forward(path)))


# TODO should not forget that we need to look at locking when multiple appends
# and even more so when adapting/compacting when loading


def read_osh_events_forward(path: Path) -> Iterator[PackedOshEvent]:
    with path.open("rt") as f:
        for i in f:
            d = json.loads(i)
            if "event" not in d:
                continue
            d = d["event"]
            yield PackedOshEvent(
                timestamp=datetime.fromisoformat(d["timestamp"]),
                command=str(d["command"]),
                duration=float(d["duration"]),
                exit_code=int(d["exit-code"]),
                folder=str(d["folder"]),
                machine=str(d["machine"]),
                session=str(d["session"]),
            )


def read_osh_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    yield from reversed(list(read_osh_events_forward(path)))


zsh_event_pattern = re.compile(
    r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$"
)


def read_zsh_events_forward(path: Path) -> Iterator[PackedOshEvent]:
    # TODO i'm not sure if all zsh history are the format as below, or does it depend on zsh settings?
    # maybe check what it looks like on a fresh system
    # and/or see that we fail if not as expected

    # TODO no expanduser anymore
    # in a way that should be gone after the zsh/python boundary
    # after all, how then would you ever have a file with actual ~ in it?
    # ok but it only does ~ at the beginning, so its not that bad
    path = path.expanduser()

    # TODO better way to iterate? one that doesnt load all in one go?
    zsh_history = enumerate(
        path.read_text(encoding="utf-8", errors="replace").split("\n")[:-1],
        start=1,
    )

    for line, content in zsh_history:
        match = zsh_event_pattern.fullmatch(content)
        if match is None:
            assert False, (path, line, json.dumps(content))
        # from what I understand, zsh_history uses a posix time stamp, utc, second resolution (floor of float seconds)
        timestamp = datetime.fromtimestamp(int(match["timestamp"]), tz=timezone.utc)
        command = match["command"]
        # note: duration in my zsh version 5.8 doesnt seem to be recorded correctly, its always 0
        # duration = int(match.group("duration"))
        while command.endswith("\\"):
            line, content = next(zsh_history)
            command = command[:-1] + "\n" + content
        yield PackedOshEvent(
            timestamp=timestamp,
            command=command,
            duration=None,
            exit_code=None,
            folder=None,
            machine=None,
            session=None,
        )


def read_zsh_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    return reversed(list(read_zsh_events_forward(path)))


def read_osh_legacy_events_forward(path: Path) -> Iterator[PackedOshEvent]:
    # TODO we dont like expanduser
    path = path.expanduser()
    # TODO we could use the faster msgspec lib, but we will probably convert this
    data = json.loads(path.read_text())

    # NOTE the legacy (pre-release) data contains events:
    # 1) imported from zsh -> we skip them, assuming there is also the actual zsh history in the archive
    # 2) osh events with time resolution in seconds -> usually see it from timestamp
    # 3) osh events with time resolution in microseconds -> usually see it from timestamp

    for entry in data:
        # NOTE imported zsh events have no session entry
        if "session" not in entry:
            continue
        # NOTE some older events had only second time resolution (timestamps, and durations)
        timestamp = datetime.fromisoformat(entry["timestamp"])
        if timestamp.microsecond == 0:
            yield PackedOshEvent(
                timestamp=timestamp,
                command=str(entry["command"]),
                duration=int(entry["duration"]),
                exit_code=int(entry["exit_code"]),
                folder=str(entry["folder"]),
                machine=str(entry["machine"]),
                session=str(entry["session"]),
            )
        else:
            yield PackedOshEvent(
                timestamp=timestamp,
                command=str(entry["command"]),
                duration=float(entry["duration"]),
                exit_code=int(entry["exit_code"]),
                folder=str(entry["folder"]),
                machine=str(entry["machine"]),
                session=str(entry["session"]),
            )


def read_osh_legacy_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    return reversed(list(read_osh_legacy_events_forward(path)))


def find_sources(base: Path) -> set[Path]:
    # TODO would be better if globbing and loaders would not be separate, could get out of sync
    return {
        *base.rglob("*.osh_legacy"),
        *base.rglob("*.zsh_history"),
        *base.rglob("*.osh"),
        *base.rglob("*.osh.msgpack"),
        *base.rglob("*.osh.msgpack.stream"),
    }


def read_events_backward(path: Path) -> Iterator[PackedOshEvent]:
    match path.suffixes:
        case [".osh_legacy"]:
            yield from read_osh_legacy_events_backward(path)
        case [".zsh_history"]:
            yield from read_zsh_events_backward(path)
        case [".osh"]:
            yield from read_osh_events_backward(path)
        case [".osh", ".msgpack"]:
            yield from read_batched_packed_osh_events_backward(path)
        case [".osh", ".msgpack", ".stream"]:
            yield from read_streamed_packed_osh_events_backward(path)
        case _ as never:
            assert False, never


# TODO maybe base should be absolute already
def load_history(base: Path) -> Iterator[PackedOshEvent]:
    active_sources = find_sources(base / "active")
    archived_sources = find_sources(base / "archive")
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
    # TODO streamed msgspec is super fast for first, but a bit slower overall, but easy to append
    # TODO but just plain msgspec is still only 3ms to first, and still fast for overall ... might just be the easier solution for now
    # that is total 1000ms vs 800ms, probably not worth it just for that for the streaming
    # but the question is how do we append, and that is the more-often operation, just appending there would be very nice
    # loading in one msgspec call is really beautifully fast for now ... we could compact it on every read, but only append on append?
    # the streaming version has a header that also gives a version, then it has a full list with length prefixed, and then it has appended events with len postfixed
    # hm we could also keep it simple streamed, but again have a cached version and we know when to stop reading the stream? if we can make the cache reading fast?
    # or only remember timestamps, and load the full stream if new, and it's the newest anyway then
    for source in active_sources:
        yield from read_events_backward(source)

    archived_mtime = max(path.stat().st_mtime for path in archived_sources)

    cached_source = base / "archived.osh.msgpack"
    if not cached_source.exists() or cached_source.stat().st_mtime < archived_mtime:
        archived = [
            event
            for source in archived_sources
            for event in read_events_backward(source)
        ]
        archived = sorted(
            archived,
            key=lambda e: e.timestamp,
            reverse=True,
        )
        write_batched_packed_osh_events(forward_events=archived, path=cached_source)
    else:
        archived = read_events_backward(cached_source)

    yield from archived

    # events = load_osh_histories(base) + load_zsh(base) + load_legacy(base)
    # TODO on this test data, sorting makes hardly any impact
    # events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # TODO msgspec is actually super fast, json is already good compared to pickle
    # and msgpack seems even a bit faster
    # so we could make the source format already this way, and we could cache things
    # we have a class cache state, and maybe even know where to continue reading active files, if needed


def threaded_worker(path: Path, queue: SimpleQueue[PackedOshEvent | None]):
    for event in read_events_backward(path):
        queue.put(event)
    queue.put(None)


def load_history_threaded(base: Path) -> Iterator[PackedOshEvent]:
    """total is the same, but time to first is slower"""
    sources = find_sources(base)
    queue: SimpleQueue[PackedOshEvent | None] = SimpleQueue()
    threads = [
        Thread(target=threaded_worker, args=(source, queue)) for source in sources
    ]
    for thread in threads:
        thread.start()
    none_count = 0
    while none_count < len(sources):
        match queue.get():
            case PackedOshEvent() as event:
                yield event
            case None:
                none_count = none_count + 1
            case _ as never:
                assert_never(never)
    for thread in threads:
        thread.join()


def process_worker(path: Path, queue: mp.Queue[PackedOshEvent | None]):
    for event in read_events_backward(path):
        queue.put(event)
    queue.put(None)


def load_history_mp(base: Path) -> Iterator[PackedOshEvent]:
    """first almost with no parallel at all, but total much slower. should send in bigger packs?"""
    sources = find_sources(base)
    queue: mp.Queue[PackedOshEvent | None] = mp.Queue()
    processes = [
        mp.Process(target=process_worker, args=(source, queue)) for source in sources
    ]
    for process in processes:
        process.start()
    none_count = 0
    while none_count < len(sources):
        match queue.get():
            case PackedOshEvent() as event:
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


def preview_from_event(event: PackedOshEvent | BaggedEvent, tz: tzinfo) -> str:
    ts = event.timestamp.astimezone(tz)
    match event:
        case PackedOshEvent(
            duration=(float(duration) | int(duration)),
            exit_code=int(exit_code),
            folder=str(folder),
            machine=str(machine),
        ):
            dt = human_duration(duration)
            parts = [
                f"[returned {exit_code} after {dt} at {ts}]",
                # TODO replace ~ again?
                f"[ran in {folder} on {machine}]",
                "",
                event.command,
            ]

        case PackedOshEvent():
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


def entry_from_event(
    event: PackedOshEvent | BaggedEvent, now: datetime, tz: tzinfo
) -> str:
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
    def from_bag(cls, command: str, bag: Sequence[PackedOshEvent]) -> BaggedEvent:
        success = sum(1 for e in bag if e.exit_code == 0)
        failure = sum(1 for e in bag if e.exit_code != 0)
        unknown = 1.0 - success - failure
        count = len(bag)
        return cls(
            # TODO which one to use here?
            timestamp=bag[0].timestamp,
            command=command,
            count=count,
            success_ratio=success / count,
            failure_ratio=failure / count,
            unknown_ratio=unknown / count,
        )


def bagged_events(events: list[PackedOshEvent]) -> list[BaggedEvent]:
    bagged: dict[str, list[PackedOshEvent]] = {}
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
    # TODO get the right file
    # TODO make it atomic, or lock. be sure we dont lose any history if writing fails in between ...
    # TODO ok i really dont like how much data we keep on writing every time we do a simple command
    path = Path("./test-data/active/base.osh.msgspec")
    events = list(read_events_backward(path))
    events.append(todo)
    write_msgpack_file(events, path)


@app.command("convert")
def app_convert(paths: list[Path]):
    for path in paths:
        match path.suffixes:
            case [".osh", ".msgpack"]:
                pass
            case _:
                # TODO also need to convert ... that doesnt quite work then
                events = read_events_backward(path)
                write_batched_packed_osh_events(
                    forward_events=sorted(events, key=lambda e: e.timestamp),
                    path=path.with_name(
                        path.name[: -sum(map(len, path.suffixes))] + ".osh.msgpack"
                    ),
                )
                path.unlink()


@app.command("convert-stream")
def app_convert_stream(paths: list[Path]):
    for path in paths:
        match path.suffixes:
            case [".osh", ".msgpack", ".stream"]:
                pass
            case _:
                events = read_events_backward(path)
                write_streamed_packed_osh_events(
                    forward_events=sorted(events, key=lambda e: e.timestamp),
                    path=path.with_name(
                        path.name[: -sum(map(len, path.suffixes))]
                        + ".osh.msgpack.stream"
                    ),
                )
                path.unlink()


# TODO bagged stuff is not as good as before, allow filtering for failed and co? order by most recent?
# TODO append event, and maybe new format?
# TODO stats :)

if __name__ == "__main__":
    app()
