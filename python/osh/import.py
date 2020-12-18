from pathlib import Path
import re
import datetime
from typing import List, Optional

import click

import osh.history
from osh.history import Event


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


def merge_zsh_into_osh(
    zsh_history: List[Event], osh_history: List[Event], timestamp_slack: float = 1.5
) -> List[Event]:
    """ merge a zsh history into an osh history
    taking special care to match double entries
        a) previously imported entries are trivial, they are exactly the same
        b) recorded by both zsh and osh are more difficult and matched with a heuristic
    """

    def is_collision(a, b):
        # todo do we record the command string exactly the same? escaping and all that?
        return (
            (a.command == b.command)
            and (a.machine == b.machine)
            and (abs((a.timestamp - b.timestamp).total_seconds()) < timestamp_slack)
        )

    merged = list(osh_history)
    for candidate in zsh_history:
        if not any(is_collision(candidate, event) for event in osh_history):
            merged.append(candidate)

    return osh.history.make(merged)


@click.group()
def cli():
    pass


@cli.command()
@click.option("--zsh-file", default="~/.zsh_history")
@click.option("--machine", required=True)
@click.option("--osh-file", default="~/.one-shell-history/events.json")
def zsh(zsh_file, machine, osh_file):

    from osh.utils import locked_file

    zsh_file = Path(zsh_file).expanduser()
    osh_file = Path(osh_file).expanduser()

    zsh_history = read_zsh_history(file=zsh_file, machine=machine)
    osh_history = osh.history.read_from_file(osh_file)

    merged = merge_zsh_into_osh(zsh_history=zsh_history, osh_history=osh_history)

    print(
        f"{len(merged)-len(osh_history)} out of {len(zsh_history)} in zsh are new to osh"
    )

    with locked_file(osh_file, wait=10):
        osh.history.write_to_file(merged, osh_file)


if __name__ == "__main__":
    cli()
