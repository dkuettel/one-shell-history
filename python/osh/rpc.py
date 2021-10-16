import inspect
import io
import json
import socket as sockets
from pathlib import Path


class RemoteException(Exception):
    pass


class Exit(Exception):
    pass


class ConnectionClosed(Exception):
    pass


def remote(method):
    def wrapper(self, *args, **kwargs):
        stream = Stream.from_path(self.socket_path)
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


def run_server(socket_path: Path, server):

    targets = {
        getattr(getattr(server, name), "__osh_rpc_name__", None): getattr(server, name)
        for name in dir(server)
    }
    targets.pop(None, None)

    socket_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sockets.socket(
            family=sockets.AF_UNIX,
            type=sockets.SOCK_STREAM,
        ) as socket:

            socket.settimeout(None)
            socket.bind(str(socket_path))
            socket.listen(1)

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
