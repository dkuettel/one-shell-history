from functools import cache, cached_property
from pathlib import Path

from osh import defaults
from osh.event_filters import EventFilter
from osh.history import Event, History
from osh.osh_files import append_event_to_osh_file
from osh.queries import BackwardsQuery, UniqueCommandsQuery


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

    def get_statistics(self):
        source = self.source
        source.refresh()
        events = source.events
        count = len(events)
        earliest = min(e.timestamp for e in events)
        most_recent = max(e.timestamp for e in events)
        return count, earliest, most_recent


class OshProxy:
    pass


class OshService:
    def __init__(self):
        pass
        # self.osh = Osh()
