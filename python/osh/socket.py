from pathlib import Path
from contextlib import contextmanager
import json
import socket
import io
import subprocess
import datetime
import os

import click

import osh.history

socket_file = Path("~/.one-shell-history/control-socket").expanduser()


@click.group()
def cli():
    pass


@cli.command()
def serve():

    history = osh.history.FromFile()

    with json_socketserver(socket_file) as accept:
        os.system("systemd-notify --ready")
        while True:
            with accept() as stream:
                message = stream.read()
                if message["command"] == "insert_event":
                    event = osh.history.Event.from_json_dict(message["event"])
                    with history.lock():
                        history.insert_event(event)
                elif message["command"] == "list_events":
                    with history.lock():
                        events = history.events
                    now = datetime.datetime.now(datetime.timezone.utc)
                    try:
                        for i, event in enumerate(
                            osh.history.aggregate_events_for_search(events)
                        ):
                            when = now - event.most_recent_timestamp
                            when = datetime.timedelta(
                                seconds=round(when.total_seconds())
                            )
                            stream.write(
                                dict(
                                    id=i,
                                    info=f"[{str(when)} ago] [{event.failed_count}/{event.occurence_count} failed]",
                                    command=event.command,
                                )
                            )
                        stream.write(0)
                    except:
                        pass
                elif message["command"] == "exit":
                    break
                else:
                    assert False


@cli.command()
@click.option("--starttime", type=int, required=True)
@click.option("--command", type=str, required=True)
@click.option("--endtime", type=int, required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--folder", type=str, required=True)
@click.option("--machine", type=str, required=True)
@click.option("--session", type=str, required=True)
def insert_event(starttime, command, endtime, exit_code, folder, machine, session):

    starttime = datetime.datetime.fromtimestamp(starttime, tz=datetime.timezone.utc)
    endtime = datetime.datetime.fromtimestamp(endtime, tz=datetime.timezone.utc)

    event = osh.history.Event(
        timestamp=starttime,
        command=command,
        duration=(endtime - starttime).total_seconds(),
        exit_code=exit_code,
        folder=folder,
        machine=machine,
        session=session,
    )

    with json_socket(socket_file) as stream:
        stream.write({"command": "insert_event", "event": event.to_json_dict()})


@cli.command()
@click.option("--query", default="")
def fzf_select(query):
    with json_socket(socket_file) as stream:
        stream.write({"command": "list_events"})
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
            history = dict()
            while fzf.poll() is None:
                event = stream.read()
                if event == 0:
                    break
                try:
                    # TODO not sure if i make all commands save with no new lines here
                    command = event["command"].replace("\n", "↪")
                    fzf.stdin.write(
                        f"{event['id']} --- {event['info']} --- {command}\n".encode(
                            "utf-8"
                        )
                    )
                    fzf.stdin.flush()
                    history[event["id"]] = event
                except:
                    break
            try:
                fzf.stdin.close()
            except:
                pass
            fzf.wait()
            try:
                selection = int(
                    fzf.stdout.read().decode("utf-8").split(" --- ", maxsplit=1)[0]
                )
                print(history[selection]["command"])
            except:
                pass


@contextmanager
def json_socketserver(socket_file: Path, parents: bool = True):
    try:
        socket_file.parent.mkdir(parents=parents, exist_ok=True)
        with socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM) as s:
            s.bind(str(socket_file))
            s.listen(1)

            @contextmanager
            def accept():
                connection, _ = s.accept()
                with connection:
                    stream = JsonStream(connection.makefile(mode="rwb"))
                    try:
                        yield stream
                    finally:
                        try:
                            stream.close()
                        except:
                            pass

            yield accept
    finally:
        if socket_file.is_socket():
            socket_file.unlink()


@contextmanager
def json_socket(socket_file: Path):
    with socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM) as s:
        s.connect(str(socket_file))
        stream = JsonStream(s.makefile(mode="rwb"))
        try:
            yield stream
        finally:
            stream.close()


class JsonStream:
    # todo stream will block stuff if it receives unfinished lines but not EOF? some timeout or something?
    def __init__(self, stream):
        self.stream = io.TextIOWrapper(stream)

    def read(self):
        return json.loads(self.stream.readline())

    def write(self, data):
        self.stream.write(json.dumps(data) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()


if __name__ == "__main__":
    cli()
