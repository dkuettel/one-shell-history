from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
from functools import cache, cached_property
from pathlib import Path

from osh import defaults, rpc
from osh.event_filters import EventFilter
from osh.history import Event, History
from osh.osh_files import append_event_to_osh_file
from osh.queries import BackwardsQuery, UniqueCommand, UniqueCommandsQuery


class Osh:
    def __init__(self, dot: Path = defaults.dot):
        self.dot = dot

    @cached_property
    def source(self):
        # TODO generally we want to call osh a history
        # rename History to a source? also move then maybe and no circular problems again
        return History(self.dot / defaults.histories)

    @cache
    def unique_commands_query(self, filter_ignored: bool):
        if filter_ignored:
            return UniqueCommandsQuery(
                self.source, EventFilter(self.dot / defaults.event_filters)
            )
        else:
            return UniqueCommandsQuery(self.source)

    def search(self, filter_failed_at, filter_ignored):
        yield from self.unique_commands_query(filter_ignored).generate_results(
            filter_failed_at
        )

    @cached_property
    def backwards_query(self):
        return BackwardsQuery(self.source)

    def search_backwards(self, session_id):
        yield from self.backwards_query.generate_results(session_id)

    def append_event(self, event: Event):
        append_event_to_osh_file(self.dot / defaults.local, event)

    def get_statistics(self) -> Statistics:
        return Statistics.from_source(self.source)


class OshProxy:
    def __init__(self, socket_path: Path = defaults.dot / defaults.socket):
        self.socket_path = socket_path

    @rpc.remote
    def search(self, stream, filter_failed_at, filter_ignored):
        stream.write((filter_failed_at, filter_ignored))
        while (command := stream.read()) is not None:
            yield UniqueCommand.from_json_dict(command)

    @rpc.remote
    def search_backwards(self, stream, session_id):
        stream.write(session_id)
        while (event := stream.read()) is not None:
            yield Event.from_json_dict(event)

    @rpc.remote
    def append_event(self, stream, event: Event):
        stream.write(event.to_json_dict())

    @rpc.remote
    def get_statistics(self, stream) -> Statistics:
        return Statistics(*stream.read())

    @rpc.remote
    def exit(self, stream):
        pass


class OshServer:
    def __init__(self, history: Osh):
        self.history = history

    @rpc.exposed
    def search(self, stream):
        filter_failed_at, filter_ignored = stream.read()
        commands = self.history.search(filter_failed_at, filter_ignored)
        for command in commands:
            stream.write(command.to_json_dict())
        # TODO this is to go into a generator, how do we detect when the generator stops reading? socket closed?
        stream.write(None)

    @rpc.exposed
    def search_backwards(self, stream):
        session_id = stream.read()
        events = self.history.search_backwards(session_id)
        for event in events:
            stream.write(event.to_json_dict())
        # TODO this is to go into a generator, how do we detect when the generator stops reading? socket closed?
        stream.write(None)

    @rpc.exposed
    def append_event(self, stream):
        self.history.append_event(Event.from_json_dict(stream.read()))

    @rpc.exposed
    def get_statistics(self, stream):
        stream.write(self.history.get_statistics())

    @rpc.exposed
    def exit(self, stream):
        raise rpc.Exit()


@dataclass
class Statistics:
    count: int
    earliest: datetime.datetime
    latest: datetime.datetime

    @classmethod
    def from_source(cls, source: History):
        source.refresh()
        return cls(
            count=len(source.events),
            earliest=min(e.timestampt for e in source.events),
            latest=max(e.timestampt for e in source.events),
        )
