from collections import Counter

from osh.history import AggregatedEvent, SearchConfig, aggregate_events


class RelevantEvents:
    """
    dont change filter_ignored after instantiation
    """

    def __init__(
        self,
        source,
        filter_failed_at: float = 1.0,
        filter_ignored: bool = True,
    ):
        self.source = source
        self.filter_failed_at = filter_failed_at
        self.filter_ignored = filter_ignored
        self.config = SearchConfig()  # TODO
        self.aggs = {}

    def as_relevant_first(self):
        from osh.osh_files import OshFileChangedMuch

        if self.source.needs_reload():
            events = self.source.get_all_events()
            self.aggs = {}
        else:
            try:
                events = self.source.get_new_events()
            except OshFileChangedMuch:
                events = self.source.get_all_events()
                self.aggs = {}

        for event in events:
            self.update(event)

        relevants = self.aggs.values()

        # TODO filter_failed_at is also more bumpy as anything can happen (up and down)
        # unless we think about the max length of after I guess
        if self.filter_failed_at is not None:
            relevants = (
                e
                for e in relevants
                if (e.fail_ratio is None) or (e.fail_ratio < self.filter_failed_at)
            )

        # TODO can we handle it somehow that not the full list needs sorting everytime
        relevants = sorted(relevants, key=lambda e: -e.occurence_count)
        return relevants

    def update(self, event):

        if not self.config.event_is_useful(event):
            return

        agg = self.aggs.get(event.command, None)

        if agg is None:
            agg = AggregatedEvent(
                most_recent_timestamp=event.timestamp,
                command=event.command,
                occurence_count=1,
                known_exit_count=0 if event.exit_code is None else 1,
                failed_exit_count=0 if event.exit_code in {0, None} else 1,
                folders=Counter({event.folder}),
                most_recent_folder=event.folder,
            )
            self.aggs[event.command] = agg

        else:
            if event.timestamp > agg.most_recent_timestamp:
                agg.most_recent_timestamp = event.timestamp
                agg.most_recent_folder = event.folder
            agg.occurence_count += 1
            if event.exit_code is not None:
                agg.known_exit_count += 1
                if event.exit_code != 0:
                    agg.failed_exit_count += 1
            agg.folders.update({event.folder})


def test_against_old_implementation():
    from pathlib import Path

    from osh.sources import IncrementalSource

    source = IncrementalSource(Path("histories"))
    assert source.needs_reload()
    events = source.get_all_events()
    events = sorted(events, key=lambda e: e.timestamp)

    old = aggregate_events(events)

    source = IncrementalSource(Path("histories"))
    rels = RelevantEvents(source)
    new = list(rels.as_relevant_first())
    newer = list(rels.as_relevant_first())
    assert new == newer

    old = sorted(
        old, key=lambda e: (-e.occurence_count, e.most_recent_timestamp, e.command)
    )
    new = sorted(
        new, key=lambda e: (-e.occurence_count, e.most_recent_timestamp, e.command)
    )

    for i, (a, b) in enumerate(zip(old, new)):
        if a != b:
            print(i)
            print(a)
            print(b)
            print()
            break

    assert old == new


def test():
    import time
    from pathlib import Path

    from osh.sources import IncrementalSource

    source = IncrementalSource(Path("histories"))
    rels = RelevantEvents(source)

    dt = time.time()
    events = list(reversed(rels.as_relevant_first()))
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {time.time()-dt}")

    print()
    dt = time.time()
    events = list(reversed(rels.as_relevant_first()))
    events = events[-10:]
    for e in events:
        print(e.occurence_count, e.command)
    print(f"took {time.time()-dt}")


if __name__ == "__main__":
    # test()
    test_against_old_implementation()
