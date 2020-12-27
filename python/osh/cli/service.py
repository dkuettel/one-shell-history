import os
import json
import socket
import io
import subprocess
from pathlib import Path
from dataclasses import dataclass
from contextlib import contextmanager
from typing import Iterable, Optional
from datetime import datetime, timezone, timedelta

import click

from osh.history import (
    History,
    LazyHistory,
    EagerHistory,
    Event,
    aggregate_events_for_search,
    AggregatedEvent,
)


@click.group()
def cli():
    pass


@cli.command()
@click.option("--control-socket", default="~/.one-shell-history/control-socket")
@click.option("--systemd-notify/--no-systemd-notify", default=True)
@click.option("--lazy-history/--no-lazy-history", default=True)
def serve(control_socket, systemd_notify, lazy_history):

    control_socket = Path(control_socket).expanduser()
    history = LazyHistory() if lazy_history else EagerHistory()

    server = Server(
        control_socket=control_socket, systemd_notify=systemd_notify, history=history
    )
    server.run()


@cli.command()
# TODO make generic decorator that also expands it and makes it path, since we use it multiple times, or make it part of the cli group and the context?
@click.option("--control-socket", default="~/.one-shell-history/control-socket")
@click.option("--starttime", type=int, required=True)
@click.option("--command", type=str, required=True)
@click.option("--endtime", type=int, required=True)
@click.option("--exit-code", type=int, required=True)
@click.option("--folder", type=str, required=True)
@click.option("--machine", type=str, required=True)
@click.option("--session", type=str, required=True)
def insert_event(
    control_socket, starttime, command, endtime, exit_code, folder, machine, session
):

    control_socket = Path(control_socket).expanduser()
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

    client = Client(control_socket)
    client.insert_event(event)


@cli.command()
@click.option("--control-socket", default="~/.one-shell-history/control-socket")
@click.option("--query", default="")
def fzf_select(control_socket, query):

    control_socket = Path(control_socket).expanduser()

    client = Client(control_socket)

    # TODO what happens if we dont finish consuming, we dont close the socket? or will the other side fail once we close it?
    events = client.list_events()
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


@contextmanager
def json_socketserver(
    socket_file: Path, parents: bool = True, timeout: Optional[float] = None
):
    # TODO this is a very simple single-threaded server
    # if ever requests get big and slow or "interactive" this will be problematic
    try:
        socket_file.parent.mkdir(parents=parents, exist_ok=True)
        with socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
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


class Exit(Exception):
    pass


@dataclass
class Server:
    control_socket: Path
    systemd_notify: bool
    history: History
    sync_interval_seconds: float = 10 * 60

    def run(self):
        with json_socketserver(
            self.control_socket, timeout=self.sync_interval_seconds
        ) as accept:

            if self.systemd_notify:
                if os.system("systemd-notify --ready") != 0:
                    raise Exception("systemd-notify failed")

            try:
                while True:
                    try:
                        with accept() as stream:
                            self.handle(stream)
                    except socket.timeout:
                        self.history.sync()
            except Exit:
                pass
            finally:
                self.history.sync()

    def handle(self, stream):
        message = stream.read()
        getattr(self, f"handle_{message['command']}")(stream, message)

    def handle_insert_event(self, stream, message):
        event = Event.from_json_dict(message["event"])
        self.history.insert_event(event)

    def handle_list_events(self, stream, message):
        events = self.history.as_list()
        events = aggregate_events_for_search(events)
        for event in events:
            stream.write(event.to_json_dict())
        stream.write(None)

    def handle_exit(self, stream, message):
        raise Exit()


@dataclass
class Client:
    control_socket: Path

    def open(self):
        return json_socket(self.control_socket)

    def insert_event(self, event: Event):
        with self.open() as stream:
            stream.write(
                dict(
                    command="insert_event",
                    event=event.to_json_dict(),
                )
            )

    def list_events(self) -> Iterable[AggregatedEvent]:
        with self.open() as stream:
            stream.write(
                dict(
                    command="list_events",
                )
            )
            while (event := stream.read()) is not None:
                yield AggregatedEvent.from_json_dict(event)

    def exit(self):
        with self.open() as stream:
            stream.write(dict(command="exit"))


if __name__ == "__main__":
    cli()
