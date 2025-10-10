from __future__ import annotations

import fcntl
import heapq
import json
import mmap
import os
import random
import re
import time
from base64 import b64encode
from collections.abc import Iterator, Sequence, Set
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum
from io import BufferedWriter
from pathlib import Path
from typing import Annotated, assert_never

import msgspec
import typer

# TODO discuss with yves the new format?


# NOTE using tag fields so we can potentially use unions later and update the data version
class Event(msgspec.Struct, frozen=True, tag_field="version", tag="v1"):
    # NOTE datetime is supported by msgspec, so I'm keeping it for now,
    # but for loading and sorting, a long int or float could be better
    # and/or we could also keep the string uninterpreted until we need it
    timestamp: datetime  # utc

    command: str

    duration: None | int | float  # seconds
    exit_code: None | int
    folder: None | str
    machine: None | str
    session: None | str


event_decoder = msgspec.msgpack.Decoder(type=Event)
encoder = msgspec.msgpack.Encoder()


def append_osh_event(event: Event, file: BufferedWriter):
    """this appends naively, it will not check for correct order"""
    data = encoder.encode(event)
    size = len(data)
    # NOTE the only entry I found in my history that is longer is actually an accidental paste
    # I'm guessing for normal useful commands, you won't type 10k characters
    if size > 2**16:
        return
    # NOTE a single append write call has a chance to be atomic
    file.write(data + size.to_bytes(length=2, byteorder="big", signed=False))


def write_osh_events(forward_events: Sequence[Event], path: Path, lock: bool):
    with path.open("wb") as f:
        if lock:
            fcntl.flock(f, fcntl.LOCK_EX)
        for event in forward_events:
            append_osh_event(event, f)


def insert_osh_event(event: Event, path: Path, lock: bool):
    """insert the new event by bubbling up from the end until the right spot is found"""
    with (
        # NOTE mmap needs a file descriptor that is opened for updating, thus the "+"
        path.open("r+b") as f,
        # NOTE length=0 means map the full file
        mmap.mmap(f.fileno(), 0) as mm,
    ):
        if lock:
            fcntl.flock(f, fcntl.LOCK_SH)

        insert_at = mm.size()

        while insert_at > 0:
            size = int.from_bytes(
                mm[insert_at - 2 : insert_at],
                byteorder="big",
                signed=False,
            )
            entry = event_decoder.decode(mm[insert_at - 2 - size : insert_at - 2])
            if entry.timestamp <= event.timestamp:
                break
            insert_at = insert_at - 2 - size

        data = encoder.encode(event)
        size = len(data)

        if insert_at < mm.size():
            mm.move(
                dest=insert_at + size + 2,
                src=insert_at,
                count=mm.size() - insert_at,
            )

        size_bytes = size.to_bytes(length=2, byteorder="big", signed=False)
        mm[insert_at : insert_at + size + 2] = data + size_bytes


def read_osh_events(path: Path, lock: bool) -> Iterator[Event]:
    with (
        # NOTE mmap needs a file descriptor that is opened for updating, thus the "+"
        path.open("r+b") as f,
        # NOTE length=0 means map the full file
        mmap.mmap(f.fileno(), 0) as mm,
    ):
        if lock:
            fcntl.flock(f, fcntl.LOCK_SH)
        at = len(mm) - 2
        while at > 0:
            size = int.from_bytes(mm[at : at + 2], byteorder="big", signed=False)
            yield event_decoder.decode(mm[at - size : at])
            at = at - size - 2


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

    # NOTE I can't say that I know this is always the zsh format
    # the way I have zsh setup makes it look like that
    # maybe this fails for other people

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

    # NOTE zsh history files are not guaranteed to be sorted
    yield from sorted(events, key=lambda event: event.timestamp, reverse=True)


def read_osh_legacy_events(path: Path) -> Iterator[Event]:
    data = json.loads(path.read_text())

    # NOTE the legacy (pre-release) data contains events:
    # 1) imported from zsh -> we skip them, assuming there is also the actual zsh history in the archive
    # 2) osh events with time resolution in seconds -> usually see it from timestamp
    # 3) osh events with time resolution in microseconds -> usually see it from timestamp

    for entry in reversed(data):
        # NOTE imported zsh events have no session entry
        # we skip them, the idea is that you also have the original zsh history in your archive
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
    # NOTE has to be in synch with read_events_from_path below
    return {
        *base.rglob("*.osh_legacy"),
        *base.rglob("*.zsh_history"),
        *base.rglob("*.osh"),
    }


def read_events_from_path(path: Path, lock: bool) -> Iterator[Event]:
    # NOTE has to be in synch with find_sources above
    match path.suffixes:
        case [".osh_legacy"]:
            yield from read_osh_legacy_events(path)
        case [".zsh_history"]:
            yield from read_zsh_events(path)
        case [".osh"]:
            yield from read_osh_events(path, lock)
        case _ as never:
            assert False, never


def read_events_from_paths(paths: Set[Path], lock: bool) -> Iterator[Event]:
    sources = [read_events_from_path(path, lock) for path in paths]
    yield from heapq.merge(
        *sources,
        key=lambda e: e.timestamp,
        reverse=True,
    )


def get_base() -> Path:
    return Path(os.environ.get("OSH_HOME", "~/.osh")).expanduser()


def read_events_from_base(base: Path) -> Iterator[Event]:
    archived_sources = find_sources(base / "archive")
    archived_sources = {path.resolve(strict=True) for path in archived_sources}

    archived_mtime = max(path.stat().st_mtime for path in archived_sources)
    cached_source = base / "archived.osh"
    if not cached_source.exists() or cached_source.stat().st_mtime < archived_mtime:
        archived = read_events_from_paths(archived_sources, lock=False)
        write_osh_events(
            forward_events=list(reversed(list(archived))),
            path=cached_source,
            lock=True,
        )

    active_sources = find_sources(base / "active")
    local_source = (base / "local.osh").resolve()
    if local_source.exists():
        active_sources = active_sources | {local_source}
    active_sources = {path.resolve(strict=True) for path in active_sources}

    sources = active_sources | {cached_source}

    # NOTE we lock all files here, but it only really works well for the archive cache and the real local one
    yield from read_events_from_paths(sources, lock=True)


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
        return f"{round(d)}D"
    if d < 365:
        return f"{round(d / 7)}W"
    y = d / 365
    return f"{round(y)}Y"


home_str = str(Path("~").expanduser())


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
            if folder.startswith(home_str):
                folder = "~" + folder[len(home_str) :]
            parts = [
                f"[returned {exit_code} after {dt} at {ts}]",
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
            cmd,
        ]
    )


@dataclass(frozen=True)
class BaggedEvent:
    timestamp: datetime  # most recent one
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
            timestamp=max(e.timestamp for e in bag),
            command=command,
            count=count,
            success_ratio=success / count,
            failure_ratio=failure / count,
            unknown_ratio=unknown / count,
        )


def bagged_events(events: list[Event]) -> list[BaggedEvent]:
    bagged = dict[str, list[Event]]()
    for event in events:
        bagged.setdefault(event.command, []).append(event)
    return [BaggedEvent.from_bag(cmd, bag) for cmd, bag in bagged.items()]


class Mode(Enum):
    all = "all"
    session = "session"
    folder = "folder"
    bag = "bag"


app = typer.Typer(pretty_exceptions_enable=False)


@app.command("search")
def app_search(
    mode: Mode | None = None,
    session: str | None = None,
    folder: str | None = None,
):
    if mode is None:
        mode = Mode.all

    events = read_events_from_base(get_base())

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


@app.command("list")
def app_list():
    events = read_events_from_base(get_base())

    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None

    for event in events:
        print(f"{event.timestamp} -- {json.dumps(event.command)}")


@app.command("bench")
def app_bench():
    """some observations
    the biggest part is deserializing, running multiprocessing doesn't help, because the pipe in between is the same problem again
    now the biggest part seems to be in stringifaction of events
    """
    start = time.perf_counter()
    events = read_events_from_base(get_base())
    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None
    print(id(next(events)))
    first = time.perf_counter()
    print(f"first after {(first-start)*1000:_}ms")
    print(sum(1 for _event in events))
    last = time.perf_counter()
    print(f"rest after {(last-first)*1000:_}ms")


@app.command("nop")
def app_nop():
    pass


@app.command("append-event")
def app_append_event(
    starttime: Annotated[float, typer.Option()],
    command: Annotated[str, typer.Option()],
    folder: Annotated[str, typer.Option()],
    endtime: Annotated[float, typer.Option()],
    exit_code: Annotated[int, typer.Option()],
    machine: Annotated[str, typer.Option()],
    session: Annotated[str, typer.Option()],
):
    path = get_base() / "local.osh"
    if path.is_symlink():
        # NOTE the target might not yet exist
        path = path.resolve(strict=False)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    timestamp = datetime.fromtimestamp(starttime, tz=timezone.utc)
    duration = float(endtime - starttime)

    event = Event(
        timestamp=timestamp,
        command=command,
        duration=duration,
        exit_code=exit_code,
        folder=folder,
        machine=machine,
        session=session,
    )

    # NOTE we want the file sorted by increasing event.timestamp
    # but we only call after the command has run, so a simple append is not good enough
    insert_osh_event(event, path, lock=True)


@app.command("convert", help="convert anything to the osh format")
def app_convert(paths: list[Path]):
    for path in paths:
        path = path.expanduser()
        match path.suffixes:
            case [".osh", ".msgpack", ".stream"]:
                pass
            case _:
                events = read_events_from_path(path, lock=True)
                new_path = path.with_name(
                    path.name[: -sum(map(len, path.suffixes))] + ".osh"
                )
                write_osh_events(
                    forward_events=sorted(events, key=lambda e: e.timestamp),
                    path=new_path,
                    lock=True,
                )
                if path != new_path:
                    path.unlink()


@app.command("convert-osh-legacy", help="convert an osh legacy file to the osh format")
def app_convert_osh_legacy(paths: list[Path]):
    for path in paths:
        path = path.expanduser()
        events = read_osh_legacy_events(path)
        new_path = path.with_name(path.name[: -sum(map(len, path.suffixes))] + ".osh")
        write_osh_events(
            forward_events=sorted(events, key=lambda e: e.timestamp),
            path=new_path,
            lock=True,
        )
        if path != new_path:
            path.unlink()


@app.command("convert-old-osh", help="convert an old osh file to the osh format")
def app_convert_old_osh(paths: list[Path]):
    for path in paths:
        path = path.expanduser()
        events = read_old_osh_events(path)
        new_path = path.with_name(path.name[: -sum(map(len, path.suffixes))] + ".osh")
        write_osh_events(
            forward_events=sorted(events, key=lambda e: e.timestamp),
            path=new_path,
            lock=True,
        )
        if path != new_path:
            path.unlink()


@app.command("report", help="report on the commander's performance")
def app_report():
    print()
    print("Hello Commander, your situation report:")
    print(flush=True)

    events = list(read_events_from_base(get_base()))

    if len(events) == 0:
        print("  No data as of yet.")

    else:
        last_event = events[0]
        first_event = events[-1]
        start = first_event.timestamp.date()
        end = last_event.timestamp.date()
        total_days = (end - start).days
        active_days_count = len({e.timestamp.date() for e in events})
        successful_event_count = sum(e.exit_code in {0, None} for e in events)
        active_day_average_event_count = successful_event_count // active_days_count
        success_rate = successful_event_count / len(events)
        failure_count = len(events) - successful_event_count

        def f(i: int) -> str:
            return f"{i:,}".replace(",", "'")

        print(f"  Our classified documents cover your history from {start} to {end}.")
        print(
            f"  You have been on active duty for {f(active_days_count)} days out of a total {f(total_days)} days in the service."
        )
        print()
        print(f"  Throughout your service you made {f(len(events))} decisions.")
        epic = random.choice(
            [
                "amazing",
                "excellent",
                "exceptional",
                "eximious",
                "extraordinary",
                "fantastic",
                "inconceivable",
                "incredible",
                "legendary",
                "marvelous",
                "mind-blowing",
                "outlandish",
                "outrageous",
                "phenomenal",
                "preposterous",
                "radical",
                "remarkable",
                "shocking",
                "striking",
                "stupendous",
                "superb",
                "surprising",
                "terrific",
                "unbelievable",
                "unheard-of",
                "unimaginable",
                "wicked",
            ]
        )
        print(
            f"  Sir, that's {'an' if epic[0] in 'aeiou' else 'a'} [3m{epic}[0m {f(active_day_average_event_count)} decisions per day when on active duty."
        )
        print()
        print(f"  Only {f(failure_count)} of your efforts have met with failure.")
        print(
            f"  Your success rate is confirmed at {round(100*success_rate)} over one hundred."
        )
    print()
    print(f"-- Good day, Commander.")


# TODO bagged stuff is not as good as before, allow filtering for failed and co? order by most recent?
# TODO what about removing duplicates? keep only the most recent one?

if __name__ == "__main__":
    app()
