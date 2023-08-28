import inspect
import json
import os
import pickle
import socket as sockets
from itertools import islice
from pathlib import Path
from typing import Any, Callable

import osh.logging as logger

# TODO not sure if those exception are still used, or useful


class RemoteException(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class NoServerException(Exception):
    pass


class Proxy:
    def __init__(self, path: Path):
        self._path = path

    def is_alive(self):
        stream = Stream.from_path(self._path)
        try:
            stream.write(("is_alive", [], {}))
            return stream.read()
        finally:
            stream.close()

    def exit(self):
        stream = Stream.from_path(self._path)
        try:
            stream.write(("exit", [], {}))
            return stream.read()
        finally:
            stream.close()

    def __getattr__(self, name):
        def call(*args, **kwargs):
            stream = Stream.from_path(self._path)

            stream.write((name, args, kwargs))
            exception, is_generator, result = stream.read()

            if exception is not None:
                stream.close()
                raise exception

            if not is_generator:
                stream.close()
                return result

            def yield_and_then_close():
                try:
                    while True:
                        stream.write(True)
                        batch = stream.read()
                        if batch == []:
                            break
                        yield from batch
                finally:
                    stream.write(None)
                    stream.close()

            return yield_and_then_close()

        return call


def run_server(socket_path: Path, server: Any, notify_systemd: bool = True):

    assert not hasattr(server, "is_alive")
    assert not hasattr(server, "exit")

    try:
        with sockets.socket(
            family=sockets.AF_UNIX,
            type=sockets.SOCK_STREAM,
        ) as socket:

            socket.settimeout(None)

            try:
                socket_path.parent.mkdir(parents=True, exist_ok=True)
                socket.bind(str(socket_path))
            except OSError as e:
                if not socket_path.is_socket():
                    raise Exception(f"There is a non-socket file at {socket_path}.")
                try:
                    stream = Stream.from_path(socket_path)
                    stream.write(("Who is it?", [], {}))
                    reply = stream.read()
                    stream.close()
                    if reply == "osh.rpc":
                        raise Exception(
                            f"There is already an rpc server running on {socket_path}"
                        )
                    raise Exception(
                        f"There is already an unknown server running on {socket_path}"
                    )
                except (ConnectionRefusedError, TimeoutError):
                    pass  # stale socket file
                socket_path.unlink()
                socket.bind(str(socket_path))

            socket.listen(10)

            # TODO not sure what is the best place to have a reasonable guarantee
            if notify_systemd:
                if os.system("systemd-notify --ready") != 0:
                    logger.warning("systemd-notify failed")

            logger.info(f"rpc listening on {socket_path}")

            while True:
                stream = Stream.from_socket(socket.accept()[0])
                try:
                    name, args, kwargs = stream.read()
                    if name == "Who is it?":
                        stream.write("osh.rpc")
                        continue
                    if name == "is_alive":
                        stream.write(True)
                        continue
                    if name == "exit":
                        stream.write(True)
                        break
                    # TODO ok was a bit stupid to have name in the same namespace as the special stuff above
                    target = getattr(server, name)
                    result = target(*args, **kwargs)
                    if inspect.isgenerator(result):
                        stream.write((None, True, None))
                        batch_size = getattr(target, "__osh_rpc_batch_size", 1000)
                        while stream.read() is not None:
                            stream.write(list(islice(result, batch_size)))
                    else:
                        stream.write((None, False, result))
                except (ConnectionError, TimeoutError, sockets.timeout) as e:
                    # TODO documentation says sockets.timeout is an alias for TimeoutError, but it doesnt work when I dont use both
                    # I think newer pythons fixed that, 3.10 or so?
                    logger.error(f"rpc call {name} failed with {e}")
                    try:
                        stream.write(e, False, None)
                    except:
                        pass
                finally:
                    stream.close()
    finally:
        if socket_path.is_socket():
            socket_path.unlink()


class Stream:
    def __init__(self, socket):
        socket.settimeout(1)
        # TODO should we also close the socket? we dont keep it now
        self.stream = socket.makefile(mode="rwb")

    @classmethod
    def from_socket(cls, socket):
        return cls(socket)

    @classmethod
    def from_path(cls, socket_path: Path):
        socket = sockets.socket(
            family=sockets.AF_UNIX,
            type=sockets.SOCK_STREAM,
        )
        socket.settimeout(1)
        socket.connect(str(socket_path))
        return cls(socket)

    def write(self, message):
        # TODO we dont actually send exceptions anymore, lets leave it? instead send a generic fail when we exit?
        pickle.dump((None, message), self.stream)
        self.stream.flush()

    def read(self):
        exception, message = pickle.load(self.stream)
        if exception is not None:
            raise RemoteException(exception)
        return message

    def write_exception(self, exception):
        pickle.dump((exception, None), self.stream)
        self.stream.flush()

    def close(self):
        try:
            # TODO socket or not?
            self.stream.close()
        except:
            pass
