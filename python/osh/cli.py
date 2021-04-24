import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import click

import osh.service
from osh import History
from osh.history import Event, LazyHistory

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
            history = History(history=LazyHistory(file=ctx.obj.events))
            yield history
        finally:
            osh.close()

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
@click.pass_context
def fzf_select(ctx, query):

    # TODO aggregate events makes it difficult to be incremental
    # unless maybe we do it backwards? especially if the preview info is computed on demand
    # feeding iteratively works, but if that means the socket stays open
    # it will block the server until fzf has finished, unless we exit once consumed

    # TODO what happens if we dont finish consuming, we dont close the socket? or will the other side fail once we close it?
    with ctx.obj.make_history() as history:
        events = history.list_events()
        # TODO here we do sync because we exit osh, that's not useful
        # osh to be smart and have a dirty flag, or it doesnt sync unless you tell it to?
    # TODO rewrite in httpserver does that nice, adapt
    # TODO from here we can merge code with the direct client, once coming as a list, once coming as iterable for responsiveness

    now = datetime.now(tz=timezone.utc)

    with subprocess.Popen(
        args=[
            "fzf",
            f"--query={query}",
            "--delimiter= --- ",
            "--with-nth=3..",  # what to display (and search)
            "--height=70%",
            "--min-height=10",
            "--layout=reverse",
            "--prompt=> ",
            "--preview-window=down:10:wrap",
            "--preview=echo {2}; echo {3..}",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as fzf:

        event_by_index = []

        for index, event in enumerate(events):

            if fzf.poll() is not None:
                break

            event_by_index.append(event)

            fzf_ago = (now - event.most_recent_timestamp).total_seconds()
            fzf_ago = timedelta(seconds=round(fzf_ago))

            fzf_info = f"[{str(fzf_ago)} ago] [{event.failed_count}/{event.occurence_count} failed]"

            # escape literal \ followed by an n so they are not expanded to a new line by fzf's preview
            fzf_command = event.command.replace("\\n", "\\\\n")
            # escape actual new lines so they are expanded to a new line by fzf's preview
            fzf_command = fzf_command.replace("\n", "\\n")
            # TODO does that take care of all types of new lines, or other dangerous characters for fzf?

            fzf_line = f"{index} --- {fzf_info} --- {fzf_command}\n"
            fzf_line = fzf_line.encode("utf-8")
            fzf.stdin.write(fzf_line)

            fzf.stdin.flush()
        try:
            # TODO maybe that only needs to happen in the for else case?
            fzf.stdin.close()
        except:
            pass
        fzf.wait()
        try:
            selection = fzf.stdout.read().decode("utf-8")
            selection = selection.split(" --- ", maxsplit=1)[0]
            selection = int(selection)
            # TODO not sure what happens with the dangling new-line here and how zsh treats it when assigning to BUFFER
            print(event_by_index[selection].command)
        except:
            pass


if __name__ == "__main__":
    cli()
