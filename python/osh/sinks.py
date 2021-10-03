from osh.history import Event
from osh.osh_file import OshFile


class Sink:
    def append_event(self, event: Event):
        raise NotImplementedError()


class OshSink(Sink):
    def __init__(self, osh_file: OshFile):
        self.osh_file = osh_file

    def append_event(self, event: Event):
        self.osh_file.append_event(event)
