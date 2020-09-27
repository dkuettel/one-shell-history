from pathlib import Path
import re
import datetime

import click

import osh.history


@click.group()
def cli():
    pass


@cli.command()
@click.option("--location", default="~/.zsh_history")
@click.option("--machine", default=None)
def zsh(location, machine):

    location = Path(location).expanduser()
    pattern = re.compile(r"^: (?P<timestamp>\d+):(?P<duration>\d+);(?P<command>.*)$")
    history = []
    zsh_history = enumerate(
        location.read_text(encoding="utf-8", errors="replace").split("\n")[:-1], start=1
    )

    for line, content in zsh_history:
        match = pattern.match(content)
        if match is None:
            print(f"cannot parse line {line} = {content.strip()}")
            continue
        timestamp = datetime.datetime.fromtimestamp(
            int(match.group("timestamp")), tz=datetime.timezone.utc
        )
        command = match.group("command")
        duration = int(match.group("duration"))
        while command.endswith("\\"):
            line, content = next(zsh_history)
            command = command[:-1] + "\n" + content
        event = osh.history.Event(
            timestamp=timestamp, command=command, duration=duration, machine=machine
        )
        history.append(event)

    history = osh.history.make(history)
    previous = osh.history.read_from_file(Path("imported.json"))
    merged = osh.history.merge([history, previous])
    osh.history.write_to_file(merged, Path("imported.json"))

    print(f"{len(set(history)-set(previous))} new events")
    print(f"{len(set(previous)-set(history))} other events")


if __name__ == "__main__":
    cli()
