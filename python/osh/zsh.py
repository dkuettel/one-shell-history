import click


@click.group()
def cli():
    pass


@cli.command()
@click.argument("starttime", type=int)
@click.argument("command", type=str)
@click.argument("endtime", type=int)
@click.argument("exit_code", type=int)
@click.argument("folder", type=str)
@click.argument("machine", type=str)
def add(starttime, command, endtime, exit_code, folder, machine):

    from pathlib import Path
    import datetime

    import osh.history as H

    file = Path("./zsh-history.json")
    history = H.read_from_file(file, or_empty=True)

    starttime = datetime.datetime.fromtimestamp(starttime, tz=datetime.timezone.utc)
    endtime = datetime.datetime.fromtimestamp(endtime, tz=datetime.timezone.utc)

    entry = H.Entry(
        timestamp=starttime,
        command=command,
        duration=(endtime - starttime).total_seconds(),
        exit_code=int(exit_code),
        folder=folder,
        machine=machine,
    )

    history = H.merge([history, [entry]])

    H.write_to_file(history, file)


@cli.command()
def ls():

    from pathlib import Path

    import osh.history as H

    file = Path("./zsh-history.json")
    history = H.read_from_file(file, or_empty=True)

    for i, entry in enumerate(reversed(history), start=1):
        print(i, entry.command.replace("\n", "..."))


@cli.command()
@click.argument("selection")
def get(selection):

    from pathlib import Path

    import osh.history as H

    file = Path("./zsh-history.json")
    history = H.read_from_file(file, or_empty=True)

    selection = int(selection.split(" ", maxsplit=1)[0])
    print(history[-selection].command)


if __name__ == "__main__":
    cli()
