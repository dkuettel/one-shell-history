import itertools
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator, Optional, Tuple

from osh.history import Event
from osh.sinks import OshSink, Sink
from osh.sources import (
    MergeInSource,
    OshLegacySource,
    OshSource,
    Source,
    UnionSource,
    ZshSource,
)


def source_from_folder_structure(base: Path) -> Source:

    active_osh_sources = DiscoveredSources(lambda: base.glob("*.osh"))
    archived_osh_sources = DiscoveredSources(
        lambda: itertools.chain(
            base.glob("archive/**/*.osh"),
            base.glob("archive/**/*.osh_legacy"),
        )
    )
    archived_other_sources = DiscoveredSources(
        lambda: itertools.chain(
            base.glob("archive/**/*.zsh_history"),
        )
    )

    osh_sources = UnionSource([active_osh_sources, archived_osh_sources])
    source = MergeInSource(main=osh_sources, other=archived_other_sources)

    # TODO this it the minimal thing that is correct, but not efficient
    # eventually: splits, mtime logic, efficient merging early and behind an mtime cache

    return source


class DiscoveredSources(Source):
    def __init__(self, discover):
        self.sources: dict[Path, Source] = {}
        self.union = UnionSource([])
        self.discover = discover

    def as_list(self) -> list[Event]:
        self.maybe_refresh()
        return self.union.as_list()

    def maybe_refresh(self):
        new_sources = {}
        for path in self.discover():
            if path in self.sources:
                new_sources[path] = self.sources[path]
            else:
                try:
                    new_sources[path] = make_source(path)
                except FileNotFoundError:
                    pass
        if set(self.sources) == set(new_sources):
            return
        self.sources = new_sources
        self.union = UnionSource(list(self.sources.values()))

    def mtime(self):
        return self.sources.mtime()


def make_source(path: Path) -> Source:
    known = {
        ".osh": OshSource,
        ".osh_legacy": OshLegacySource,
        ".zsh_history": ZshSource,
    }
    if path.suffix not in known:
        raise Exception(f"unknown suffix in {path}")
    return known[path.suffix](path)


if __name__ == "__main__":
    source = source_from_folder_structure(Path("histories"))
    events = source.as_list()
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
