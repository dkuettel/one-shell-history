import math
import os
import random
import socket as sockets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import click

from osh import Osh, defaults, rpc
from osh.fzf import fzf
from osh.history import Event
from osh.rpc import NoServerException
from osh.utils import seconds_to_slang, str_mark_trailing_spaces

_direct = None
_proxy = None


def get_history_direct():
    global _direct
    if _direct is None:
        _direct = Osh()
    return _direct


def get_history_proxy():
    global _proxy
    if _proxy is None:
        try:
            _proxy = rpc.Proxy(defaults.dot / defaults.socket)
            _proxy.is_alive()
        except Exception as e:
            _proxy = e
            raise e
    if isinstance(_proxy, Exception):
        raise _proxy
    return _proxy


def get_history_proxy_or_direct():
    global _proxy

    if _proxy is None:
        try:
            _proxy = rpc.Proxy(defaults.dot / defaults.socket)
            _proxy.is_alive()
        except Exception as e:
            print(
                f"Warning: Using direct mode, cannot access osh service @{_proxy._path.resolve()}, {e}.",
                file=sys.stderr,
            )
            _proxy = e

    if not isinstance(_proxy, Exception):
        return _proxy

    return get_history_direct()


def get_history():
    raise NotImplementedError()


@click.group()
@click.option(
    "--use-service/--no-use-service",
    "-s/-d",
    default=bool(os.environ.get("__osh_use_service", True)),
)
def cli(use_service):
    global get_history
    get_history = get_history_proxy_or_direct if use_service else get_history_direct


def format_aggregated_events(events):

    now = datetime.now(tz=timezone.utc)

    for index, event in enumerate(events):

        # TODO this is also a bit slow
        fzf_ago = seconds_to_slang((now - event.most_recent_timestamp).total_seconds())

        if event.fail_ratio is None:
            fzf_failed = "no fail statistics"
        else:
            fzf_failed = f"{event.fail_ratio:.0%} failed"
        fzf_info = f"[{fzf_ago} ago] [{event.occurrence_count} calls, {fzf_failed}]"

        fzf_folders1 = f"[most recent folder: {event.most_recent_folder}]"
        # TODO this is slow, also we dont need to send the full list of folders, only aggregated is good enough, less data to send, already aggregated too
        # especially the most_common(3) is slow
        fzf_folders2 = (
            "[" + ", ".join(f"{c}x {f}" for f, c in event.folders.most_common(3)) + "]"
        )

        # escape literal \ followed by an n so they are not expanded to a new line by fzf's preview
        fzf_command = event.command.replace("\\n", "\\\\n")
        # escape actual new lines so they are expanded to a new line by fzf's preview
        fzf_command = fzf_command.replace("\n", "\\n")
        fzf_command = str_mark_trailing_spaces(fzf_command)
        # TODO does that take care of all types of new lines, or other dangerous characters for fzf?

        yield f"{index} --- {fzf_info} --- {fzf_folders1} --- {fzf_folders2} --- {fzf_command}"


def search_aggregated(
    history,
    query: str = "",
    filter_failed: bool = True,
    filter_ignored: bool = True,
    expect: tuple[str, ...] = (),
    header: Optional[str] = None,
) -> Tuple[str, str, str]:

    events = []

    def gen_events():
        results = history.search(
            filter_failed_at=1.0 if filter_failed else None,
            filter_ignored=filter_ignored,
        )
        for event in results:
            events.append(event)
            yield event

    formatted = format_aggregated_events(gen_events())

    all_expect = expect + ("enter", "ctrl-c", "esc")
    result = fzf(
        formatted,
        query=query,
        print_query=True,
        delimiter=" --- ",
        with_nth="5..",  # what to display (and search)
        height="70%",
        min_height="10",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {3}; echo {4}; echo; echo {5..}",
        expect=",".join(all_expect),
        tiebreak="index",
        header=header,
        # TODO --read0 and we could have newlines in the data? also then --print0?
        # lets see how it looks with newlines, could be convenient
        # not just useful for command that have new lines (which we need to escape now)
        # also for meta info like folders would be easier now
    )
    del formatted  # this lets the osh service know that we can stop streaming results

    assert result.key in all_expect
    if result.key == "enter" and result.selection is None:
        selection = result.query
    elif result.key == "enter" and result.selection is not None:
        index = int(result.selection.split(" --- ", maxsplit=1)[0])
        event = events[index]
        selection = event.command
    elif result.key in {"ctrl-c", "esc"}:
        selection = result.query
    else:
        selection = None
    # TODO took away ctrl-x to add to ignore

    return selection, result.query, result.key if result.key in expect else None


def search_backwards(
    history,
    query: str = "",
    session_id: Optional[str] = None,
    session_start: Optional[datetime] = None,
    expect: Tuple[str, ...] = (),
    header: Optional[str] = None,
) -> Tuple[str, str, str]:

    events = history.search_backwards(
        session_id=session_id,
        session_start=session_start,
    )

    # TODO from outside?
    now = datetime.now(tz=timezone.utc)
    event_by_index = []

    def generate():
        for index, event in enumerate(events):

            event_by_index.append(event)

            fzf_ago = seconds_to_slang((now - event.timestamp).total_seconds())

            fzf_info = f"[{fzf_ago} ago] [exit={event.exit_code}]"

            # escape literal \ followed by an n so they are not expanded to a new line by fzf's preview
            fzf_command = event.command.replace("\\n", "\\\\n")
            # escape actual new lines so they are expanded to a new line by fzf's preview
            fzf_command = fzf_command.replace("\n", "\\n")
            # TODO does that take care of all types of new lines, or other dangerous characters for fzf?

            yield f"{index} --- {fzf_info} --- {index+1:#2d}# {fzf_ago:>4s} ago --- {fzf_command}"

    all_expect = expect + ("enter", "ctrl-c", "esc")
    result = fzf(
        generate(),
        query=query,
        print_query=True,
        delimiter=" --- ",
        with_nth="3..",  # what to display (and search)
        nth="2..",  # what to search in the displayed part
        height="70%",
        min_height="10",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {4..}",
        tiebreak="index",
        expect=",".join(all_expect),
        header=header,
        # TODO make a prompt? header good enough to spot the mode?
    )
    # TODO make this with a context to be more clear
    del events  # this lets the osh service know that we can stop streaming results

    assert result.key in all_expect
    if result.key == "enter" and result.selection is None:
        selection = result.query
    elif result.key == "enter" and result.selection is not None:
        index = int(result.selection.split(" --- ", maxsplit=1)[0])
        event = event_by_index[index]
        selection = event.command
    elif result.key in {"ctrl-c", "esc"}:
        selection = result.query
    else:
        selection = None

    return selection, result.query, result.key if result.key in expect else None


@cli.command()
@click.option(
    "--modes",
    "--mode",
    default="backwards,backwards-session,aggregated-filtered,aggregated-all",
)
@click.option("--query", default="")
@click.option("--session-id", default=None)
@click.option("--session-start", type=float, default=None)
def search(modes, query, session_id, session_start):

    modes = modes.split(",")

    if session_start is not None:
        session_start = datetime.fromtimestamp(session_start, tz=timezone.utc)

    def is_possible(m):
        if m == "backwards-session":
            return session_id is not None
        return True

    modes = [m for m in modes if is_possible(m)]

    if len(modes) == 0:
        modes = ["backwards"]
    mode_index = 0

    expect = ("tab", "shift-tab")

    history = get_history()

    while True:
        mode = modes[mode_index]
        # active mode is marked with inverted colors
        header = " ".join(f"[7m{m}[0m" if m == mode else m for m in modes)

        if mode == "backwards":
            selection, query, key = search_backwards(
                history,
                query,
                expect=expect,
                header=header,
            )
        elif mode == "backwards-session":
            selection, query, key = search_backwards(
                history,
                query,
                session_id,
                session_start,
                expect=expect,
                header=header,
            )
        elif mode == "aggregated-filtered":
            selection, query, key = search_aggregated(
                history,
                query,
                True,
                True,
                expect=expect,
                header=header,
            )
        elif mode == "aggregated-all":
            selection, query, key = search_aggregated(
                history,
                query,
                False,
                False,
                expect=expect,
                header=header,
            )
        else:
            assert False, mode

        if selection is not None:
            print(selection)
            return

        if key == "tab":
            mode_index = (mode_index + 1) % len(modes)
        elif key == "shift-tab":
            mode_index = (mode_index - 1) % len(modes)
        else:
            assert False, key


@cli.command()
@click.option("--timestamp", type=float)
@click.option("--prefix", type=str, default=None)
@click.option("--ignore", type=str, default=None)
@click.option("--session-id", type=str, default=None)
@click.option("--session-start", type=float, default=None)
def previous_event(timestamp, prefix, ignore, session_id, session_start):

    tolerance = 1e-8
    timestamp = datetime.fromtimestamp(timestamp - tolerance, tz=timezone.utc)

    # TODO note we dont yet pass that info, the zsh glue code needs to record the start of the session for this
    if session_start is not None:
        session_start = datetime.fromtimestamp(session_start, tz=timezone.utc)

    history = get_history()
    event = history.previous_event(timestamp, prefix, ignore, session_id, session_start)

    if event is None:
        sys.exit(1)

    print(f"{event.timestamp.timestamp():021.9f} {event.command}")
    # NOTE: in zsh use x=$(osh previous-event ...) and then $x[1,21] or $x[23,-1] to get both outputs


@cli.command()
@click.option("--timestamp", type=float)
@click.option("--prefix", type=str, default=None)
@click.option("--ignore", type=str, default=None)
@click.option("--session-id", type=str, default=None)
@click.option("--session-start", type=float, default=None)
def next_event(timestamp, prefix, ignore, session_id, session_start):

    tolerance = 1e-8
    timestamp = datetime.fromtimestamp(timestamp + tolerance, tz=timezone.utc)

    # TODO note we dont yet pass that info, the zsh glue code needs to record the start of the session for this
    if session_start is not None:
        session_start = datetime.fromtimestamp(session_start, tz=timezone.utc)

    history = get_history()
    event = history.next_event(timestamp, prefix, ignore, session_id, session_start)

    if event is None:
        sys.exit(1)

    print(f"{event.timestamp.timestamp():021.9f} {event.command}")
    # NOTE: in zsh use x=$(osh previous-event ...) and then $x[1,21] or $x[23,-1] to get both outputs


@cli.command()
@click.option("--starttime", type=float, required=True)
@click.option("--command", type=str, required=True)
@click.option("--endtime", type=float, required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--folder", type=str, required=True)
@click.option("--machine", type=str, required=True)
@click.option("--session", type=str, required=True)
def append_event(
    starttime,
    command,
    endtime,
    exit_code,
    folder,
    machine,
    session,
):

    history = get_history()

    starttime = datetime.fromtimestamp(starttime, tz=timezone.utc)
    endtime = datetime.fromtimestamp(endtime, tz=timezone.utc)

    event = Event(
        timestamp=starttime,
        command=command,
        duration=(endtime - starttime).total_seconds(),
        exit_code=exit_code,
        folder=folder,
        machine=machine,
        session=session,
    )

    history.append_event(event)


@cli.command()
def stats():
    history = get_history()
    s = history.get_statistics()
    print()
    print("Hello Commander, your situation report:")
    print()
    if s is None:
        print("  No data as of yet.")
    else:
        total_days = round(
            (s.last_event.timestamp - s.first_event.timestamp).total_seconds()
            / (60 * 60 * 24)
        )
        start = s.first_event.timestamp.date()
        end = s.last_event.timestamp.date()
        print(f"  Our classified documents cover your history from {start} to {end}.")
        print(
            f"  You have been on active duty for {s.active_days_count:,} days out of a total {total_days:,} days in the service."
        )
        print()
        print(f"  Throughout your service you made {s.event_count:,} decisions.")
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
            f"  Sir, that's {'an' if epic[0] in 'aeiou' else 'a'} [3m{epic}[0m {s.active_day_average_event_count} decisions per day when on active duty."
        )
        print()
        print(f"  Only {s.failure_count:,} of your efforts have met with failure.")
        print(
            f"  Your success rate is confirmed at {round(100*s.success_rate)} over one hundred."
        )
    print()
    print(f"-- Good day, Commander.")


@cli.command()
def run_server():
    import osh.logging as logger

    try:
        logger.info(f"open direct history from {defaults.dot}")
        history = get_history_direct()
        logger.info("start server")
        rpc.run_server(defaults.dot / defaults.socket, history)
    except:
        logger.info("server failed")
        raise
    finally:
        logger.info("server exits")


@cli.command()
def is_server_alive():
    try:
        history = get_history_proxy()
        print(f"Server on {defaults.socket} is alive.")
    except:
        print(f"Server on {defaults.socket} is not alive.")
        sys.exit(1)


@cli.command()
def stop_server():
    get_history_proxy().exit()
