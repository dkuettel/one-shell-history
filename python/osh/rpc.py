import inspect
import io
import json
import os
import socket as sockets
from pathlib import Path
from typing import Callable


class RemoteException(Exception):
    pass


class Exit(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class NoServerException(Exception):
    pass


def remote(method):
    def wrapper(self, *args, **kwargs):
        try:
            stream = Stream.from_path(self.socket_path)
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as e:
            raise NoServerException from e
        stream.write(method.__name__)
        result = method(self, stream, *args, **kwargs)

        if not inspect.isgenerator(result):
            stream.close()
            return result

        def yield_and_close():
            try:
                yield from result
            finally:
                stream.close()

        return yield_and_close()

    return wrapper


def exposed(method):
    method.__osh_rpc_name__ = method.__name__
    return method


def run_server(socket_path: Path, server, notify_systemd: bool = True):

    targets: dict[str, Callable] = {}
    for name in dir(server):
        member = getattr(server, name)
        target = getattr(member, "__osh_rpc_name__", None)
        if target is None:
            continue
        targets[target] = member

    assert "Who is it?" not in targets
    targets["Who is it?"] = lambda stream: stream.write("osh.rpc")

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
                    stream.write("Who is it?")
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
                    print("warning: systemd-notify failed")

            while True:
                stream = Stream.from_socket(socket.accept()[0])
                try:
                    targets[stream.read()](stream)
                except Exit:
                    break
                except Exception as e:
                    # TODO this seems to suppress the exception?!
                    # try:
                    #     stream.write_exception(e)
                    # except:
                    #     pass
                    raise e
                finally:
                    stream.close()
    finally:
        if socket_path.is_socket():
            socket_path.unlink()


class Stream:
    def __init__(self, socket):
        socket.settimeout(1)
        # TODO should we also close the socket? we dont keep it now
        self.stream = io.TextIOWrapper(socket.makefile(mode="rwb"))

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
        self.stream.write(json.dumps((None, message)) + "\n")
        self.stream.flush()

    def read(self):
        try:
            reply = self.stream.readline()
            if reply == "":
                raise ConnectionClosed()
            exception, message = json.loads(reply)
        except json.JSONDecodeError as e:
            raise Exception(f"Malformed reply from rpc server: {json.dumps(reply)}")
        if exception is not None:
            raise RemoteException(exception)
        return message

    def write_exception(self, exception):
        self.stream.write(json.dumps((str(exception), None)) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()
