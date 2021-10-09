from pathlib import Path

from osh.history import Event
from osh.osh_files import append_event_to_osh_file


class Sink:
    def append_event(self, event: Event):
        raise NotImplementedError()


class OshSink(Sink):
    def __init__(self, path: Path):
        self.path = path

    def append_event(self, event: Event):
        append_event_to_osh_file(self.path, event)
