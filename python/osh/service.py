import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from osh import History, jsonp
from osh.history import AggregatedEvent, Event


@dataclass
class OshService:
    path: Path
    history: History

    def run(self):
        server = jsonp.Server(path=self.path, handler=self.handler)
        # TODO not sure what is the best place to have a reasonable guarantee
        if os.system("systemd-notify --ready") != 0:
            print("warning: systemd-notify failed")
        server.run()

    def handler(self, stream):
        message = stream.read()
        getattr(self, f"handle_{message}")(stream)

    def handle_get_sync_interval(self, stream):
        stream.write(self.history.sync_interval)

    def handle_set_sync_interval(self, stream):
        self.history.sync_interval = stream.read()

    def handle_insert_event(self, stream):
        event = Event.from_json_dict(stream.read())
        self.history.insert_event(event)

    def handle_aggregate_events(self, stream):
        kwargs = stream.read()
        for event in self.history.aggregate_events(**kwargs):
            stream.write(event.to_json_dict())
        stream.write(None)

    def handle_list_backwards(self, stream):
        session = stream.read()
        for event in self.history.list_backwards(session):
            stream.write(event.to_json_dict())
        stream.write(None)

    def handle_stop(self, stream):
        print("exit")
        raise jsonp.Exit()


class OshProxy:
    def __init__(self, path: Path):
        self._path = path

    def _stream(self):
        return jsonp.connect(self._path)

    @property
    def sync_interval(self):
        with self._stream() as stream:
            stream.write("get_sync_interval")
            return stream.read()

    @sync_interval.setter
    def sync_interval(self, value):
        with self._stream() as stream:
            stream.write("set_sync_interval")
            stream.write(value)

    def insert_event(self, event):
        with self._stream() as stream:
            stream.write("insert_event")
            stream.write(event.to_json_dict())

    def aggregate_events(self, **kwargs):
        with self._stream() as stream:
            stream.write("aggregate_events")
            stream.write(kwargs)
            while True:
                event = stream.read()
                if event is None:
                    break
                yield AggregatedEvent.from_json_dict(event)

    def list_backwards(self, session=None):
        with self._stream() as stream:
            stream.write("list_backwards")
            stream.write(session)
            while True:
                event = stream.read()
                if event is None:
                    break
                yield Event.from_json_dict(event)

    def stop(self):
        with self._stream() as stream:
            stream.write("stop")


def run(path: Path, history: History):
    service = OshService(path=path, history=history)
    service.run()


def connect(path: Path) -> OshProxy:
    return OshProxy(path)
