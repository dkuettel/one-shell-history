from __future__ import annotations

import datetime
import itertools
import sqlite3
from pathlib import Path
from typing import Optional

from osh.history import Event, merge_other_into_main
from osh.sources import read_any_file


class SqlHistory:
    def __init__(self, source: SqlSource):
        self.source = source

    def search_backwards(self, session_id: Optional[str] = None):
        self.source.maybe_refresh()
        if session_id is None:
            query = "select * from events order by timestamp desc"
        else:
            query = "select * from events where session={session_id} order by timestamp desc"
        for row in self.source.con.execute(query):
            yield Event(
                datetime.datetime.fromtimestamp(row[0], tz=datetime.timezone.utc),
                *row[1:],
            )

    def search(self, filter_failed_at=1.0, filter_ignored=True):
        from osh.queries import UniqueCommand
        from collections import Counter

        self.source.maybe_refresh()
        query = (
            "select "
            "min(timestamp) as most_recent_timestamp, "
            "command, count(*) as occurrence_count, "
            "count(exit_code) as known_exit_count, "
            "sum(exit_code!=0) as failed_exit_count, "
            "group_concat(distinct folder) as folders, "
            "folder as most_recent_folder "
            "from events "
            "group by command "
            "order by occurrence_count desc "
        )
        for row in self.source.con.execute(query):
            yield UniqueCommand(
                datetime.datetime.fromtimestamp(row[0], tz=datetime.timezone.utc),
                row[1],
                row[2],
                row[3],
                row[4],
                Counter(),
                None,
            )


class SqlSource:
    def __init__(self, histories_path: Path, sql_path: Path):
        self.histories_path = histories_path
        self.sql_path = sql_path
        self.con = sqlite3.Connection(self.sql_path)

    def __del__(self):
        self.con.close()

    def maybe_refresh(self):
        db = self.get_db_archive_signatures()
        fs = self.get_fs_archive_signatures()
        if db != fs:
            self.refresh(fs)
        # TODO incremental loading for active sources

    def get_db_archive_signatures(self):
        self.con.execute(
            "create table if not exists archive_signatures (path text, mtime real, size integer)"
        )
        rows = self.con.execute("select * from archive_signatures")
        # TODO is mtime python float the same accuracy as sql real?
        return {(Path(row[0]), row[1], row[2]) for row in rows}

    def get_fs_archive_signatures(self):
        def sig(f):
            assert not f.is_symlink()
            stat = f.stat()
            return (f, stat.st_mtime, stat.st_size)

        path = self.histories_path / "archive"
        globs = ["**/*.osh", "**/*.osh_legacy", "**/*.zsh_history"]
        return {sig(f) for glob in globs for f in path.glob(glob)}

    def get_fs_active_signatures(self):
        def sig(f):
            assert not f.is_symlink()
            stat = f.stat()
            return (f, stat.st_mtime, stat.st_size)

        return {sig(f) for f in self.histories_path.glob("*.osh")}

    def refresh(self, fs_archive_signatures=None):

        print("... reindex history ...")

        # TODO need to see how to lock here or so for the time of the full update

        if fs_archive_signatures is None:
            fs_archive_signatures = self.get_fs_archive_signatures()
        fs_active_signatures = self.get_fs_active_signatures()

        osh_events = []
        other_events = []

        def maybe_load(s):
            try:
                f = s[0]
                if f.suffix in {".osh", ".osh_legacy"}:
                    events = osh_events
                elif f.suffix in {".zsh_history"}:
                    events = other_events
                else:
                    assert False, f
                events.extend(read_any_file(f))
                return True
            except FileNotFoundError:
                return False

        fs_archive_signatures = {s for s in fs_archive_signatures if maybe_load(s)}
        fs_active_signatures = {s for s in fs_active_signatures if maybe_load(s)}

        all_events = merge_other_into_main(other_events, osh_events)

        self.reset_events(all_events)
        self.reset_archive_signatures(fs_archive_signatures)
        self.reset_active_signatures(fs_active_signatures)
        self.con.commit()

    def reset_events(self, events):
        tuples = (
            (
                e.timestamp.timestamp(),
                e.command,
                e.duration,
                e.exit_code,
                e.folder,
                e.machine,
                e.session,
            )
            for e in events
        )
        self.con.execute("drop table if exists events")
        self.con.execute(
            "create table events "
            "(timestamp real, command text, duration integer, "
            "exit_code integer, folder text, machine text, session text)"
        )
        self.con.executemany("insert into events values (?,?,?,?,?,?,?)", tuples)

    def reset_archive_signatures(self, signatures):
        self.con.execute("drop table if exists archive_signatures")
        self.con.execute(
            "create table archive_signatures (path text, mtime real, size integer)"
        )
        tuples = ((str(s[0]), s[1], s[2]) for s in signatures)
        self.con.executemany("insert into archive_signatures values (?,?,?)", tuples)

    def reset_active_signatures(self, signatures):
        self.con.execute("drop table if exists active_signatures")
        self.con.execute(
            "create table active_signatures (path text, mtime real, size integer)"
        )
        tuples = ((str(s[0]), s[1], s[2]) for s in signatures)
        self.con.executemany("insert into active_signatures values (?,?,?)", tuples)


if __name__ == "__main__":
    source = SqlSource(Path("histories"), Path("history.sqlite3"))
    history = SqlHistory(source)
    events = list(history.search_backwards())
    print(events[:10])
