import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import click

import osh.service
from osh import History
from osh.fzf import fzf
from osh.history import Event, LazyHistory, SearchConfig
from osh.utils import seconds_to_slang, str_mark_trailing_spaces

default_folder = Path("~/.one-shell-history").expanduser()


@dataclass
class Config:
    folder: Optional[Path] = None
    make_history: Optional[Callable[[], History]] = None

    @property
    def events_path(self):
        return self.folder / "events.json"

    @property
    def socket_path(self):
        return self.folder / "service.socket"


def make_path(ctx, param, value):
    return Path(value)


@click.group()
@click.option("--folder", callback=make_path, default=default_folder)
@click.pass_context
def cli(ctx, folder):
    ctx.obj = Config(folder=folder)


@cli.group()
@click.pass_context
def direct(ctx):
    @contextmanager
    def make_history():
        try:
            history = History(history=LazyHistory(file=ctx.obj.events_path))
            yield history
        finally:
            history.close()

    ctx.obj.make_history = make_history


@cli.group()
@click.pass_context
def service(ctx):
    @contextmanager
    def make_history():
        yield osh.service.connect(ctx.obj.socket_path)

    ctx.obj.make_history = make_history


@service.command()
@click.pass_context
def run(ctx):
    try:
        history = History(
            history=LazyHistory(file=ctx.obj.events_path),
            sync_interval=5 * 60,
        )
        osh.service.run(ctx.obj.socket_path, history)
    finally:
        history.close()


@service.command()
@click.pass_context
def stop(ctx):
    osh.service.connect(ctx.obj.socket_path).stop()


def commands(fn):
    c = click.command()(fn)
    direct.add_command(c)
    service.add_command(c)
    return c


@commands
@click.option("--starttime", type=int, required=True)
@click.option("--command", type=str, required=True)
@click.option("--endtime", type=int, required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--folder", type=str, required=True)
@click.option("--machine", type=str, required=True)
@click.option("--session", type=str, required=True)
@click.pass_context
def insert_event(
    ctx,
    starttime,
    command,
    endtime,
    exit_code,
    folder,
    machine,
    session,
):

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

    with ctx.obj.make_history() as history:
        history.insert_event(event)


@commands
@click.option("--query", default="")
@click.option("--filter-failed/--no-filter-failed", default=True)
@click.pass_context
def fzf_select(ctx, query, filter_failed):

    # TODO aggregate events makes it difficult to be incremental
    # unless maybe we do it backwards? especially if the preview info is computed on demand
    # feeding iteratively works, but if that means the socket stays open
    # it will block the server until fzf has finished, unless we exit once consumed

    # TODO what happens if we dont finish consuming, we dont close the socket? or will the other side fail once we close it?
    with ctx.obj.make_history() as history:
        events = history.aggregate_events(
            filter_failed_at=1.0 if filter_failed else None
        )
        # TODO here we do sync because we exit osh, that's not useful
        # osh to be smart and have a dirty flag, or it doesnt sync unless you tell it to?
    # TODO rewrite in httpserver does that nice, adapt
    # TODO from here we can merge code with the direct client, once coming as a list, once coming as iterable for responsiveness

    now = datetime.now(tz=timezone.utc)
    event_by_index = []

    def generate():
        nonlocal event_by_index

        for index, event in enumerate(events):

            event_by_index.append(event)

            fzf_ago = seconds_to_slang(
                (now - event.most_recent_timestamp).total_seconds()
            )

            if event.fail_ratio is None:
                fzf_failed = "no fail statistics"
            else:
                fzf_failed = f"{event.fail_ratio:.0%} failed"
            fzf_info = f"[{fzf_ago} ago] [{event.occurence_count} calls, {fzf_failed}]"

            # escape literal \ followed by an n so they are not expanded to a new line by fzf's preview
            fzf_command = event.command.replace("\\n", "\\\\n")
            # escape actual new lines so they are expanded to a new line by fzf's preview
            fzf_command = fzf_command.replace("\n", "\\n")
            fzf_command = str_mark_trailing_spaces(fzf_command)
            # TODO does that take care of all types of new lines, or other dangerous characters for fzf?

            yield f"{index} --- {fzf_info} --- {fzf_command}"

    result = fzf(
        generate(),
        query=query,
        delimiter=" --- ",
        with_nth="3..",  # what to display (and search)
        height="70%",
        min_height="10",
        layout="reverse",
        prompt="> ",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {3..}",
        print_query=True,
        expect="enter,ctrl-c,ctrl-x",
        # TODO --read0 and we could have newlines in the data? also then --print0?
    )

    index = int(result.selection.split(" --- ", maxsplit=1)[0])
    event = event_by_index[index]

    if result.key == "enter":
        print(event.command)

    elif result.key == "ctrl-c":
        print(query)

    elif result.key == "ctrl-x":
        # TODO just as a POC loading here, ultimately probably cached or something, and locked
        search_config = SearchConfig()
        search_config.add_ignored_command(event.command)
        # TODO how to give result.query as the new query?
        fzf_select.invoke(ctx)

    else:
        print(f"unknown exit key {result.key}")

    # TODO other options
    # execute-*, reload
    # but we could also just tell osh, and then redo, an outer-loop reload
    # or if osh is globally available, then just much easier pipe? if command has unique identifiers
    # but we might lose functionality


@commands
@click.option("--session", required=True)
@click.pass_context
def fzf_select_session_backwards(ctx, session):

    with ctx.obj.make_history() as history:
        events = history.list_session_backwards(session)

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
        query="",
        delimiter=" --- ",
        with_nth="3..",  # what to display (and search)
        nth="2..",  # what to search in the displayed part
        height="70%",
        min_height="10",
        layout="default",
        prompt="> ",
        preview_window="down:10:wrap",
        preview="echo {2}; echo {4..}",
        tiebreak="index",
        expect="enter,ctrl-c",
    )

    index = int(result.selection.split(" --- ", maxsplit=1)[0])
    event = event_by_index[index]

    if result.key == "enter":
        print(event.command)
    elif result.key == "ctrl-c":
        print()
    else:
        print(f"unknown exit key {result.key}")


if __name__ == "__main__":
    cli()
