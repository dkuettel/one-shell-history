from pathlib import Path
from contextlib import contextmanager
import json
import socket
import io

import click

import osh.history

socket_file = Path("control-socket")


@click.group()
def cli():
    pass


@cli.command()
def server():

    history = osh.history.FromFile()

    with json_socketserver(socket_file) as accept:
        while True:
            with accept() as stream:
                message = stream.read()
                if message["command"] == "add_event":
                    print(message["arguments"])
                elif message["command"] == "list_events":
                    with history.edit():
                        for event in history.events:
                            stream.write(event.command)
                        stream.write(0)
                else:
                    assert False


@cli.command()
def client():

    with json_socket(socket_file) as stream:
        stream.write({"command": "list_events"})
        while True:
            event = stream.read()
            if event == 0:
                break
            print(event)


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
                        stream.close()

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
