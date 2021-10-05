import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from osh.history import Event
from osh.osh_file import OshFile
from osh.sources import OshLegacySource, OshSource, Source, UnionSource


class ArchivedOshSources(Source):
    def __init__(self, path: Path):
        self.path = path
        self.signature = None
        self.events = None

    def as_list(self) -> list[Event]:
        self.maybe_refresh()
        return list(self.events)

    def maybe_refresh(self):
        has_data = self.events is not None
        candidates = set(discover_candidates(self.path, ["osh", "osh-legacy"]))
        is_same_signature = self.signature == candidates
        if has_data and is_same_signature:
            return
        signature, sources = maybe_load_candidates(candidates)
        self.signature = set(signature)
        self.events = UnionSource(sources).as_list()


@dataclass
class Candidate:
    path: Path
    mtime: float


def discover_candidates(path: Path, suffixes: list[str]) -> list[Candidate]:
    return [
        Candidate(p, p.stat().st_mtime)
        for suffix in suffixes
        for p in path.glob(f"**/{suffix}")
    ]


def maybe_load_candidates(
    candidates: list[Candidate],
) -> Tuple[list[Candidate], list[Source]]:
    def maybe_load(c):
        try:
            if c.path.suffix == "osh":
                source = OshSource(OshFile(c.path))
            elif c.path.suffix == "osh-legacy":
                source = OshLegacySource(c.path)
            else:
                assert False, c.path
            return source.as_list()
        except FileNotFoundError:
            return None

    sources = [maybe_load(c) for c in candidates]
    candidates = [c for c, s in zip(candidates, sources) if s is not None]

    return candidates, sources
