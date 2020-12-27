import click


@click.group()
def cli():
    pass


@cli.command()
@click.option("--starttime", type=int, required=True)
@click.option("--command", type=str, required=True)
@click.option("--endtime", type=int, required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--folder", type=str, required=True)
@click.option("--machine", type=str, required=True)
@click.option("--session", type=str, required=True)
def insert_event(starttime, command, endtime, exit_code, folder, machine, session):

    from datetime import datetime, timezone

    from osh.history import EagerHistory, Event

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

    history = EagerHistory()
    history.insert_event(event)


@cli.command()
@click.option("--query", default="")
def fzf_select(query):

    import subprocess
    from datetime import datetime, timedelta, timezone

    from osh.history import EagerHistory, aggregate_events_for_search

    history = EagerHistory()
    events = history.as_list()
    events = aggregate_events_for_search(events)

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

        for index, event in enumerate(events):

            if fzf.poll() is not None:
                break

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
            print(events[selection].command)
        except:
            pass


if __name__ == "__main__":
    cli()
