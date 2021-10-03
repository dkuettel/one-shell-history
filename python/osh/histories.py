from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from osh.osh_file import OshFile
from osh.sinks import OshSink, Sink
from osh.sources import OshLegacySource, OshSource, Source, UnionSource, ZshSource


def histories_from_folder_structure(
    basefolder: Path, sink_path: Path
) -> Tuple[Sink, Source]:
    """return a single sink and source (usually a Union) to work with as a data backend
    basefolder will be created if it does not exist
    any histories found will be loaded based on extension
    basefolder/archive and subfolders contains anything that is not active
    they will be loaded but not watched for file content changes
    new file additions to basefolder/archive however will be picked up
    "*.osh" files directly in basefolder root are considered to be active and they are watched for change
    non-"*.osh" files in root are ignored
    sink_path is relative to basefolder, this is where new events are appended to
    do not have two different instances (local or remote) of osh write to the same osh sink file
    sink_path is initialized empty if it does not exist
    """

    osh_file = OshFile(basefolder / sink_path)
    if not osh_file.exists():
        # TODO wheres a good place to init? fail if not there?
        osh_file.create(machine="todo")

    osh_sink = OshSink(osh_file)
    osh_source = OshSource(osh_file)

    # TODO we dont watch yet in case archive gets new files added (not file content changes though)
    # simplest implementation just reloads completely on potential changes
    sources = (
        [osh_source]
        + discover_active_osh_sources(basefolder, ignore=basefolder / sink_path)
        + discover_archived_osh_sources(basefolder)
    )
    merge_sources = discover_archived_other_sources(basefolder)

    # TODO maybe we need to cascade unions differently to efficiently handle updates and changes
    full_source = UnionSource(sources=sources, merge_sources=merge_sources)

    return osh_sink, full_source


def discover_active_osh_sources(
    basefolder: Path, ignore: Optional[Path] = None
) -> list[OshSource]:
    return [
        OshSource(OshFile(file)) for file in basefolder.glob("*.osh") if file != ignore
    ]


def discover_archived_osh_sources(basefolder: Path) -> list[Source]:
    return [
        OshSource(OshFile(file)) for file in basefolder.glob("archive/**/*.osh")
    ] + [OshLegacySource(file) for file in basefolder.glob("archive/**/*.osh-legacy")]


def discover_archived_other_sources(basefolder: Path) -> list[Source]:
    return [ZshSource(file) for file in basefolder.glob("archive/**/*.zsh_history")]


if __name__ == "__main__":
    sink, source = histories_from_folder_structure(
        Path("histories"), Path("base.osh")
    )
    events = source.as_list()
    print(f"{len(events)=}")
    print(f"{events[-1]=}")
