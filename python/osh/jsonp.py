from __future__ import annotations

""" client server json messages protocol with unix domain sockets
I would like to replace this with another library, but didnt find a good solution:
- python stdlib xmlrpc is clunky
- python multiprocessing with Managers doesnt handle generators
- a flask or werkzeug WSGI is painful, WSGI servers dont have a nice interface to exit plus unix domain sockets are bumpy there
- 'rpyc' is quite good and transparent, too transparent, it keeps every Event in a list remote and it gets slow
"""

import io
import json
import socket as S
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class JsonSocket:
    def __init__(self, socket):
        self.socket = socket
        self.stream = io.TextIOWrapper(socket.makefile(mode="rwb"))

    def read(self):
        # TODO maybe we need a timeout here if the client dies mid-request? or does the connection die anyway then?
        # TODO indeed it doesnt seem to be robust when the other side closes the connection unexpectedly, or catch higher up?
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
    path: Path
    handler: Callable[[JsonSocket], None]
    timeout: Optional[float] = None

    def run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            with S.socket(family=S.AF_UNIX, type=S.SOCK_STREAM) as server:

                server.settimeout(self.timeout)
                server.bind(str(self.path))
                server.listen(1)  # TODO probably ok currently with single thread

                while True:
                    socket, address = server.accept()
                    try:
                        stream = JsonSocket(socket)
                        self.handler(stream)
                    finally:
                        stream.close()
                        socket.close()

        except Exit:
            pass
        finally:
            if self.path.is_socket():
                self.path.unlink()


@contextmanager
def connect(path: Path) -> JsonSocket:
    with S.socket(family=S.AF_UNIX, type=S.SOCK_STREAM) as socket:
        socket.connect(str(path))
        stream = JsonSocket(socket)
        try:
            yield stream
        finally:
            stream.close()
