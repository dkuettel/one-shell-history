import datetime
import json
from pathlib import Path

from osh.history import Event

default_file = Path("~/.one-shell-history/history.osh")


class OshFile:
    """
    the format is json lines
    the file extension is osh, eg, history.osh
    the file only grows in lines, should make it easy to append and read when watching
    entries contain any of those keys
        format, machine, description, event
    all but event overwrite a previous setting, event appends to events
    event contains the keys
        timestamp, command, duration, exit-code, folder, session
    the only format currently is "osh-history-v1"
    """

    # TODO this is a draft, it doesnt cache yet, but should eventually
    # append_event not atomic yet, but we do assume this is the only instance writing, so should be fine for now
    # keep threading outside of this class, some main loop or something should take care of it
    # other sources could be generically decorated and only reload if file changed?
    # this one here could read from the same file all the time, since we only append
    # same inode, or same name and reseek?
    # then probably dont even need watchdog? or does watchdog also queue events?
    # currently reload could happen on-demand, like when ctrl-r or so, later maybe preemptive?

    def __init__(self, file: Path = default_file):
        self.file = file

    def as_list(self) -> list[Event]:

        file = self.file.expanduser()

        meta_format = "osh-history-v1"
        meta_machine = None
        meta_description = None
        events = []

        with file.open("rt") as lines:
            for line in lines:
                if line == "\n":
                    continue
                line = json.loads(line)

                if "format" in line:
                    meta_format = line["format"]
                    assert meta_format == "osh-history-v1"

                if "machine" in line:
                    meta_machine = line["machine"]

                if "description" in line:
                    meta_description = line["description"]

                if "event" in line:
                    assert meta_machine is not None
                    events.append(event_from_json_dict(line["event"], meta_machine))

        return events

    def append_event(self, event: Event):
        file = self.file.expanduser()
        json_str = json.dumps(event_to_json_dict(event))
        with file.open("at") as f:
            f.write(json_str + "\n")


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


def event_from_json_dict(jd: dict, machine: str) -> Event:
    # TODO sanity check that nothing is None?
    return Event(
        timestamp=datetime.datetime.fromisoformat(jd["timestamp"]),
        command=jd["command"],
        duration=jd["duration"],
        exit_code=jd["exit-code"],
        folder=jd["folder"],
        machine=machine,
        session=jd["session"],
    )
