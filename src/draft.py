from __future__ import annotations

import heapq
import json
import mmap
import re
import time
from base64 import b64encode
from collections.abc import Iterator, Sequence, Set
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from pathlib import Path
from typing import assert_never

import msgspec
from typer import Typer


# TODO discuss with yves a new format?
# NOTE using tag fields so we can potentially use unions later and update the data version
# TODO seems much bigger than the original data, because of the none default values? at least when checking on how archive gets bundled
class Event(msgspec.Struct, frozen=True, tag_field="version", tag="v1"):
    # TODO could save this as an easy thing, good enough for sorting, and only make smart when needed?
    # TODO or we could sort on the string too? float or big int would be better
    # TODO maybe assert self.timestamp.tzinfo is datetime.timezone.utc ?
    # TODO some have only second resolution here (just like duration)
    # TODO could use year, month, ... just the input to datetime, and make clear it is utc. then ordering is almost native. tuple vs not?
    timestamp: datetime  # utc

    command: str

    duration: None | int | float  # seconds
    exit_code: None | int
    folder: None | str
    machine: None | str
    session: None | str


event_decoder = msgspec.msgpack.Decoder(type=Event)


def write_osh_events(forward_events: Sequence[Event], path: Path):
    # TODO here there is no benefit giving the type already? but we could just have one ready globally?
    encoder = msgspec.msgpack.Encoder()
    with path.open("wb") as f:
        for event in forward_events:
            data = encoder.encode(event)
            count = len(data)
            # TODO need to think about the maximum size here, ran into it at least once with 2 bytes now
            if count > 2**16:
                # TODO getting one entry with 125518 bytes. ok i think it's fine, this is a useless command for the history, let's stick with 2 bytes
                # hmm unless we write some multiline script? still, at the border
                # print(f"{count} bytes are too many: {event}")
                continue
            f.write(data)
            f.write(count.to_bytes(length=2, byteorder="big", signed=False))


def append_osh_event(event: Event, path: Path):
    data = msgspec.msgpack.encode(event)
    count = len(data)
    # TODO need to think about the maximum size here, ran into it at least once with 2 bytes now
    if count > 2**16:
        # TODO getting one entry with 125518 bytes. ok i think it's fine, this is a useless command for the history, let's stick with 2 bytes
        # hmm unless we write some multiline script? still, at the border
        # print(f"{count} bytes are too many: {event}")
        return  # TODO raise? silent is a bit bad
    with path.open("ab") as f:
        # NOTE a single append write call has a chance to be atomic
        f.write(data + count.to_bytes(length=2, byteorder="big", signed=False))


def read_osh_events(path: Path) -> Iterator[Event]:
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
            yield event_decoder.decode(mm[at - count : at])
            at = at - count - 2


# TODO should not forget that we need to look at locking when multiple appends
# and even more so when adapting/compacting when loading


def read_old_osh_events(path: Path) -> Iterator[Event]:
    events = []
    with path.open("rt") as f:
        for i in f:
            d = json.loads(i)
            if "event" not in d:
                continue
            d = d["event"]
            events.append(
                Event(
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                    command=str(d["command"]),
                    duration=float(d["duration"]),
                    exit_code=int(d["exit-code"]),
                    folder=str(d["folder"]),
                    machine=str(d["machine"]),
                    session=str(d["session"]),
                )
            )
    yield from reversed(events)


def read_zsh_events(path: Path) -> Iterator[Event]:
    zsh_event_pattern = re.compile(
        r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$"
    )

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

    events = []

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
        events.append(
            Event(
                timestamp=timestamp,
                command=command,
                duration=None,
                exit_code=None,
                folder=None,
                machine=None,
                session=None,
            )
        )

    yield from reversed(events)


def read_osh_legacy_events(path: Path) -> Iterator[Event]:
    # TODO we dont like expanduser
    path = path.expanduser()
    # TODO we could use the faster msgspec lib, but we will probably convert this
    data = json.loads(path.read_text())

    # NOTE the legacy (pre-release) data contains events:
    # 1) imported from zsh -> we skip them, assuming there is also the actual zsh history in the archive
    # 2) osh events with time resolution in seconds -> usually see it from timestamp
    # 3) osh events with time resolution in microseconds -> usually see it from timestamp

    for entry in reversed(data):
        # NOTE imported zsh events have no session entry
        if "session" not in entry:
            continue
        # NOTE some older events had only second time resolution (timestamps, and durations)
        timestamp = datetime.fromisoformat(entry["timestamp"])
        if timestamp.microsecond == 0:
            yield Event(
                timestamp=timestamp,
                command=str(entry["command"]),
                duration=int(entry["duration"]),
                exit_code=int(entry["exit_code"]),
                folder=str(entry["folder"]),
                machine=str(entry["machine"]),
                session=str(entry["session"]),
            )
        else:
            yield Event(
                timestamp=timestamp,
                command=str(entry["command"]),
                duration=float(entry["duration"]),
                exit_code=int(entry["exit_code"]),
                folder=str(entry["folder"]),
                machine=str(entry["machine"]),
                session=str(entry["session"]),
            )


def find_sources(base: Path) -> set[Path]:
    # TODO would be better if globbing and loaders would not be separate, could get out of sync
    return {
        *base.rglob("*.osh_legacy"),
        *base.rglob("*.zsh_history"),
        *base.rglob("*.osh"),
        *base.rglob("*.osh.msgpack.stream"),
    }


def read_events_from_path(path: Path) -> Iterator[Event]:
    match path.suffixes:
        case [".osh_legacy"]:
            yield from read_osh_legacy_events(path)
        case [".zsh_history"]:
            yield from read_zsh_events(path)
        case [".osh"]:
            yield from read_osh_events(path)
        case _ as never:
            assert False, never


def read_events_from_paths(paths: Set[Path]) -> Iterator[Event]:
    sources = [read_events_from_path(path) for path in paths]
    # TODO especially if we can do it in parallel threaded or so, new python abilities to use here?
    yield from heapq.merge(
        *sources,
        key=lambda e: e.timestamp,
        reverse=True,
    )


def read_events_from_base(base: Path) -> Iterator[Event]:
    archived_sources = find_sources(base / "archive")
    archived_mtime = max(path.stat().st_mtime for path in archived_sources)
    cached_source = base / "archived.osh"
    if not cached_source.exists() or cached_source.stat().st_mtime < archived_mtime:
        archived = read_events_from_paths(archived_sources)
        # write_batched_packed_osh_events(forward_events=archived, path=cached_source)
        write_osh_events(
            forward_events=list(reversed(list(archived))), path=cached_source
        )

    active_sources = find_sources(base / "active")

    yield from read_events_from_paths({*active_sources, cached_source})


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
        case Event(
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

        case Event():
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
        success = sum(1 for e in bag if e.exit_code == 0)
        failure = sum(1 for e in bag if e.exit_code != 0)
        count = len(bag)
        unknown = count - success - failure
        return cls(
            # TODO which one to use here?
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


@app.command("search")
def app_search(
    mode: Mode | None = None,
    session: str | None = None,
    folder: str | None = None,
):
    if mode is None:
        mode = Mode.all

    events = read_events_from_base(Path("test-data"))

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
    events = read_events_from_base(Path("test-data"))
    # events = load_history_threaded(Path("test-data"))
    # events = load_history_mp(Path("test-data"))
    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None
    # print(len(entry_from_event(next(events), now, local_tz)))
    print(id(next(events)))
    first = time.perf_counter()
    print(f"first after {(first-start)*1000:_}ms")
    # TODO ok now the majority of time spent is actually the stringification. save cached as string, other tricks?
    # print(sum(len(entry_from_event(event, now, local_tz)) for event in events))
    print(sum(1 for event in events))
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
    event = todo()
    path = Path("./test-data/active/base.osh.msgspec.stream")
    append_osh_event(event, path)


@app.command("convert", help="convert anything to the osh format")
def app_convert(paths: list[Path]):
    for path in paths:
        match path.suffixes:
            case [".osh", ".msgpack", ".stream"]:
                pass
            case _:
                events = read_events_from_path(path)
                new_path = path.with_name(
                    path.name[: -sum(map(len, path.suffixes))] + ".osh"
                )
                write_osh_events(
                    forward_events=sorted(events, key=lambda e: e.timestamp),
                    path=new_path,
                )
                if path != new_path:
                    path.unlink()


@app.command("convert-osh-legacy", help="convert an osh legacy file to the osh format")
def app_convert_osh_legacy(paths: list[Path]):
    for path in paths:
        events = read_osh_legacy_events(path)
        new_path = path.with_name(path.name[: -sum(map(len, path.suffixes))] + ".osh")
        write_osh_events(
            forward_events=sorted(events, key=lambda e: e.timestamp),
            path=new_path,
        )
        if path != new_path:
            path.unlink()


@app.command("convert-old-osh", help="convert an old osh file to the osh format")
def app_convert_old_osh(paths: list[Path]):
    for path in paths:
        events = read_old_osh_events(path)
        new_path = path.with_name(path.name[: -sum(map(len, path.suffixes))] + ".osh")
        write_osh_events(
            forward_events=sorted(events, key=lambda e: e.timestamp),
            path=new_path,
        )
        if path != new_path:
            path.unlink()


# TODO bagged stuff is not as good as before, allow filtering for failed and co? order by most recent?
# TODO what about removing duplicates? keep only the most recent one?
# TODO append event, and maybe new format?
# TODO stats :)

if __name__ == "__main__":
    app()
