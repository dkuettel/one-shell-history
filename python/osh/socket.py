from pathlib import Path
from contextlib import contextmanager
import json
import socket
import io
import subprocess
import datetime

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
        while True:
            with accept() as stream:
                message = stream.read()
                if message["command"] == "insert_event":
                    event = osh.history.Event.from_json_dict(message["event"])
                    with history.lock():
                        history.insert_event(event)
                elif message["command"] == "list_events":
                    with history.lock():
                        try:
                            for event in osh.history.generate_pruned_for_search(
                                history.events
                            ):
                                stream.write(event.command)
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
def fzf_select():
    with json_socket(socket_file) as stream:
        stream.write({"command": "list_events"})
        with subprocess.Popen(
            args=[
                "fzf",
                "--reverse",
                "--height=50%",
                "--nth=2..",
                "--preview-window=down:8:wrap",
                "--preview=echo {}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ) as fzf:
            history = []
            while fzf.poll() is None:
                event = stream.read()
                if event == 0:
                    break
                try:
                    fzf.stdin.write(
                        (
                            f"{len(history)} # " + event.replace("\n", "...") + "\n"
                        ).encode("utf-8")
                    )
                    fzf.stdin.flush()
                    history.append(event)
                except:
                    break
            try:
                fzf.stdin.close()
            except:
                pass
            fzf.wait()
            try:
                selection = int(
                    fzf.stdout.read().decode("utf-8").split(" ", maxsplit=1)[0]
                )
                print(history[selection])
            except:
                pass


@contextmanager
def json_socketserver(socket_file: Path):
    try:
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
