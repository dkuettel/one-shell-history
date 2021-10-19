import math
import os
import socket as sockets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from osh import Osh, OshProxy, OshServer, defaults, rpc
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
            _proxy = OshProxy()
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
            _proxy = OshProxy()
            _proxy.is_alive()
        except Exception as e:
            print(
                f"Warning: Using direct mode, cannot access osh service @{_proxy.socket_path.resolve()}, {e}.",
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

        fzf_ago = seconds_to_slang((now - event.most_recent_timestamp).total_seconds())

        if event.fail_ratio is None:
            fzf_failed = "no fail statistics"
        else:
            fzf_failed = f"{event.fail_ratio:.0%} failed"
        fzf_info = f"[{fzf_ago} ago] [{event.occurrence_count} calls, {fzf_failed}]"

        fzf_folders1 = f"[most recent folder: {event.most_recent_folder}]"
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


@cli.command()
@click.option("--query", default="")
@click.option("--filter-failed/--no-filter-failed", default=True)
@click.option("--filter-ignored/--no-filter-ignored", default=True)
@click.pass_context
def search(ctx, query, filter_failed, filter_ignored):

    history = get_history()

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

    result = fzf(
        formatted,
        query=query,
        delimiter=" --- ",
        with_nth="5..",  # what to display (and search)
        height="70%",
        min_height="10",
        layout="reverse",
        prompt="agg> " if filter_ignored else "all> ",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {3}; echo {4}; echo; echo {5..}",
        print_query=True,
        expect="enter,ctrl-c,ctrl-x,ctrl-r",
        tiebreak="index",
        # TODO --read0 and we could have newlines in the data? also then --print0?
        # lets see how it looks with newlines, could be convenient
        # not just useful for command that have new lines (which we need to escape now)
        # also for meta info like folders would be easier now
    )
    del formatted  # this lets the osh service know that we can stop streaming results

    if result.key == "enter":
        if result.selection is None:
            print()
        else:
            index = int(result.selection.split(" --- ", maxsplit=1)[0])
            event = events[index]
            print(event.command)

    elif result.key == "ctrl-c":
        print(query)

    elif result.key == "ctrl-x":
        # TODO just as a POC loading here, ultimately probably cached or something, and locked
        if result.selection is None:
            ctx.invoke(search, query=result.query or "")
        else:
            index = int(result.selection.split(" --- ", maxsplit=1)[0])
            event = events[index]
            # TODO currently not working
            search_config = SearchConfig()
            search_config.add_ignored_command(event.command)
            ctx.invoke(search, query=result.query or "")

    elif result.key == "ctrl-r":
        # switch between filter ignore and show all
        ctx.invoke(
            search,
            query=result.query or "",
            filter_failed=not filter_ignored,
            filter_ignored=not filter_ignored,
        )

    else:
        assert False, result.key

    # TODO other options
    # execute-*, reload
    # but we could also just tell osh, and then redo, an outer-loop reload
    # or if osh is globally available, then just much easier pipe? if command has unique identifiers
    # but we might lose functionality


@cli.command()
@click.option("--query", default="")
@click.option("--session/--global", default=True)
@click.option("--session-id", default=None)
@click.option("--session-start", type=float, default=None)
@click.pass_context
def search_backwards(ctx, query, session, session_id, session_start):

    history = get_history()

    session = session and (session_id is not None)

    # TODO note we dont yet pass that info, the zsh glue code needs to record the start of the session for this
    if session_start is not None:
        session_start = datetime.fromtimestamp(session_start, tz=timezone.utc)

    events = history.search_backwards(
        session_id=session_id if session else None,
        # session_start=session_start if session else None,
        session_start=session_start,
    )

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

    result = fzf(
        generate(),
        query=query,
        delimiter=" --- ",
        with_nth="3..",  # what to display (and search)
        nth="2..",  # what to search in the displayed part
        height="70%",
        min_height="10",
        layout="default",
        prompt="session> " if session else "global> ",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {4..}",
        tiebreak="index",
        expect="enter,ctrl-c,ctrl-e",
    )
    del events  # this lets the osh service know that we can stop streaming results

    if result.key == "enter":
        if result.selection is None:
            print()
        else:
            index = int(result.selection.split(" --- ", maxsplit=1)[0])
            event = event_by_index[index]
            print(event.command)
    elif result.key == "ctrl-c":
        print()
    elif result.key == "ctrl-e":
        # switch between per-session and global
        ctx.invoke(
            search_backwards,
            query=result.query or "",
            session=not session,
            session_id=session_id,
        )
    else:
        assert False, result.key


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
    if s.count == 0:
        print("  No data as of yet.")
    else:
        days = max(
            1,
            math.ceil((s.latest - s.earliest).total_seconds() / (60 * 60 * 24)),
        )
        per_day = round(s.count / days)
        print(f"  - {s.count:,} events")
        print(f"  - over {days:,} days")
        print(f"  - between {s.earliest.date()} and {s.latest.date()}")
        print()
        print(f"Sir, that's an incredible {per_day} commands per day,")
        print(
            f"at a confirmed success rate of {round(100*s.success_rate)} over one hundred!"
        )
    print()
    print(f"        -- Good day, Commander.")


@cli.command()
def run_server():
    history = get_history_direct()
    server = OshServer(history)
    # TODO note in systemd we are piped and by default it buffers a lot, so we dont see messages
    # anyway use a proper logger, then not an issue? how does logger and systemd go together?
    print("start server", flush=True)
    rpc.run_server(defaults.dot / defaults.socket, server)
    print("server exits", flush=True)


@cli.command()
def stop_server():
    get_history_proxy().exit()


@cli.command()
def profile_lines():
    from line_profiler import LineProfiler as Profile

    # https://github.com/pyutils/line_profiler
    # pip install line_profiler
    # kernprof -l python/osh/__main__.py profile , when used with @profile, builtins magic
    # python -m line_profiler -u 1 __main__.py.lprof

    global history

    profiler = Profile(history.aggregate_events)

    with profiler:
        _profile()

    profiler.dump_stats("lines.prof")
    profiler.print_stats(stream=sys.stdout, output_unit=1)


@cli.command()
def profile_std():
    from cProfile import Profile
    from pstats import Stats

    # https://docs.python.org/3/library/profile.html

    with Profile(subcalls=True, builtins=True) as p:
        _profile()

    stats = Stats(p)
    stats.dump_stats("std.prof")
    stats.sort_stats("cumulative", "tottime")
    stats.reverse_order()
    stats.print_stats("/osh/")
    # stats.print_callers("/osh/")
    # stats.print_callees("/osh/")

    # to visualize
    # pip install snakeviz
    # snakeviz std.prof

    # to visualize, looks better
    # pip install tuna
    # tuna std.prof

    # to visualize, looks professional
    # pip install pyprof2calltree
    # sudo apt install kcachegrind
    # pyprof2calltree -i cachegrind.out -o cachegrind.out -k


@cli.command()
def profile_pp():
    from pprofile import Profile, StatisticalProfile

    # https://github.com/vpelletier/pprofile
    # pip install pprofile
    # has deterministic and statistical
    # by default the output is overly complete
    # command line call looks very robust, probably better than this here
    # deterministic
    p = Profile()  # deterministic

    # statistical
    # they say dont use it for something that only runs a few seconds
    # p = StatisticalProfile()

    with p():
        _profile()

    p.print_stats()
    with open("cachegrind.out-pp", "wt") as f:
        p.callgrind(f)
        # filename=set() to limit to interesting files

    # print_stats is similar to line_profiler
    # and cachegrind as above again
    # cant quite make sense of cachegrind output with this one


@cli.command()
def profile():
    # for use with generic outside calls
    # like, eg, pyinstrument -r html -m osh profile
    # maybe use -t, I find this one also hard to read
    # pip install pyinstrument, https://pyinstrument.readthedocs.io/en/latest/guide.html

    _profile()


def _profile():

    global history
    dt = time.time()
    events = history.aggregate_events(
        filter_failed_at=1.0,
        filter_ignored=True,
    )
    events = list(events)
    print(f"first in {time.time()-dt}")
    dt = time.time()
    events = history.aggregate_events(
        filter_failed_at=1.0,
        filter_ignored=True,
    )
    events = list(events)
    print(f"second in {time.time()-dt}")
    # formatted = list(format_aggregated_events(events))
    # print(formatted)
