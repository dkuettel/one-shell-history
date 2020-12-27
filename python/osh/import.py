import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import click

import osh.history
from osh.history import Event, print_events


def read_zsh_history(file: Path, machine: Optional[str] = None) -> List[Event]:

    pattern = re.compile(r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$")

    history = []
    zsh_history = enumerate(
        file.read_text(encoding="utf-8", errors="replace").split("\n")[:-1], start=1
    )

    for line, content in zsh_history:
        match = pattern.match(content)
        if match is None:
            print(f"cannot parse line {line} = {content.strip()}")
            continue
        # from what I understand, .zsh_history uses a posix time stamp, utc, second resolution
        timestamp = datetime.datetime.fromtimestamp(
            int(match["timestamp"]), tz=datetime.timezone.utc
        )
        command = match["command"]
        # note: duration in my zsh version 5.8 doesnt seem to be recorded correctly, its always 0
        # duration = int(match.group("duration"))
        while command.endswith("\\"):
            line, content = next(zsh_history)
            command = command[:-1] + "\n" + content
        event = Event(timestamp=timestamp, command=command, machine=machine)
        history.append(event)

    return osh.history.make(history)


def is_same_event(a: Event, b: Event, slack: float):
    # TODO do we record the command string exactly the same? escaping and all that?
    # alternatively compare first timestamp, and if that one is exact,
    # use loose heuristics with rest
    return (
        (a.command == b.command)
        and (a.machine == b.machine)
        and (abs((a.timestamp - b.timestamp).total_seconds()) < slack)
    )


def contains_same_event(candidate: Event, events: List[Event], slack: float):
    return any(is_same_event(candidate, e, slack=slack) for e in events)


def find_new_zsh_events(
    zsh_history: List[Event], osh_history: List[Event], slack: float = 1.5
) -> List[Event]:
    return [
        e for e in zsh_history if not contains_same_event(e, osh_history, slack=slack)
    ]


@click.group()
def cli():
    pass


@cli.command()
@click.option("--zsh-file", default="~/.zsh_history")
@click.option("--machine", required=True)
@click.option("--osh-file", default="~/.one-shell-history/events.json")
def zsh(zsh_file, machine, osh_file):

    # TODO could import also cleanup earlier mistakes maybe?

    from osh.utils import locked_file

    zsh_file = Path(zsh_file).expanduser()
    osh_file = Path(osh_file).expanduser()

    zsh_history = read_zsh_history(file=zsh_file, machine=machine)
    osh_history = osh.history.read_from_file(osh_file)

    news = find_new_zsh_events(zsh_history=zsh_history, osh_history=osh_history)

    print_events(news)
    print()

    click.confirm("Add these new events from zsh to osh?", abort=True)

    merged = osh.history.merge([osh_history, news])

    with locked_file(osh_file, wait=10):
        osh.history.write_to_file(merged, osh_file)


if __name__ == "__main__":
    cli()
