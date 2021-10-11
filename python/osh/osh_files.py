import datetime
import json
from pathlib import Path
from typing import Iterable

from osh.history import Event

""" the osh file format
json lines
file extension is osh, eg, history.osh
file only grows in lines, should make it easy to append and read when watching
only lines with a close new-line are ready to be read
entries contain any of those keys
    format, description, event
all but event overwrite a previous setting, event appends to events
a new format takes effect already in the current line
event contains the keys
    timestamp, command, duration, exit-code, folder, machine, session
the only format currently is "osh-history-v1"
"""


def create_osh_file(file: Path):
    file = file.expanduser()
    header = {
        "format": "osh-history-v1",
        "description": None,
    }
    file.write_text(json.dumps(header) + "\n")


def read_osh_file(file: Path):
    return list(OshFileReader(file).read_events())


def append_event_to_osh_file(file: Path, event: Event):
    file = file.expanduser()
    json_str = json.dumps({"event": event_to_json_dict(event)})
    with file.open("at") as f:
        f.write(json_str + "\n")


def read_osh_legacy_file(file: Path, skip_imported: bool = True):
    file = file.expanduser()
    data = json.loads(file.read_text())

    # NOTE the legacy (pre-release) data contains events:
    # 1) imported from zsh -> usually skip
    # 2) osh events with time resolution in seconds -> usually see it from timestamp
    # 3) osh events with time resolution in microseconds -> usually see it from timestamp

    # TODO Event.from_json_dict will go away, then need to do it here explicitely for the future
    events = [Event.from_json_dict(event) for event in data]

    if skip_imported:
        events = [e for e in events if e.session is not None]

    return events


class OshFileChangedMuch(Exception):
    pass


class OshFileReader:
    def __init__(self, file: Path):
        self.file = file
        self.last_file = None
        self.last_mtime = None
        self.last_size = None
        self.last_tell = None
        self.last_line = None

    def read_events(self) -> Iterable[Event]:

        file = self.file.resolve()
        stat = file.stat()

        # TODO is it more robust to open the file and work on the file descriptor?
        # this way we for sure read the content of the stats we just checked

        if self.last_file is None:
            self.last_file = file
            self.last_mtime = stat.st_mtime
            self.last_size = stat.st_size
            self.last_tell = 0
            self.last_line = None
            return self._generate()

        if self.last_file != file:
            raise OshFileChangedMuch(self.file)

        if self.last_mtime != stat.st_mtime and self.last_size < stat.st_size:
            self.last_mtime = stat.st_mtime
            self.last_size = stat.st_size
            return self._generate()

        if self.last_mtime != stat.st_mtime and self.last_size >= stat.st_size:
            raise OshFileChangedMuch(self.file)

        def nothing():
            yield from []

        return nothing()

    def _generate(self):

        file_format = "osh-history-v1"

        with self.last_file.open("rt") as file:

            file.seek(self.last_tell)
            if self.last_line is not None:
                if self.last_line != file.readline():
                    raise OshFileChangedMuch(self.file)

            while (line := file.readline()).endswith("\n"):

                self.last_tell = file.tell()
                self.last_line = line

                line = json.loads(line)

                file_format = line.pop("format", file_format)
                assert file_format == "osh-history-v1"

                line.pop("description", None)

                if "event" in line:
                    yield event_from_json_dict(line.pop("event"))

                if len(line) > 0:
                    raise Exception(f"unexpected content left: {line}")


def event_to_json_dict(event: Event) -> dict:
    jd = dict()
    jd["timestamp"] = event.timestamp.isoformat(timespec="microseconds")
    jd["command"] = event.command
    assert event.duration is not None
    jd["duration"] = event.duration
    assert event.exit_code is not None
    jd["exit-code"] = event.exit_code
    assert event.folder is not None
    jd["folder"] = event.folder
    assert event.machine is not None
    jd["machine"] = event.machine
    assert event.session is not None
    jd["session"] = event.session
    return jd


def event_from_json_dict(jd: dict) -> Event:
    # TODO sanity check that nothing is None?
    return Event(
        timestamp=datetime.datetime.fromisoformat(jd["timestamp"]),
        command=jd["command"],
        duration=jd["duration"],
        exit_code=jd["exit-code"],
        folder=jd["folder"],
        machine=jd["machine"],
        session=jd["session"],
    )
