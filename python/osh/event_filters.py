import json
import math
import re
from pathlib import Path

import yaml


class NoEventFilter:
    def __init__(self):
        self.revision = 0
        self.success_return_codes = {0}

    def refresh(self):
        pass

    def discard(self, event):
        return False


def maybe_create_event_filter_config_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(
        r"""format: osh-event-filters-v1
success_return_codes: [0]
ignore-commands: # list commands you dont want to find in your search results (exact string matches)
  - top
ignore-patterns: # list command patterns you dont want to find in your search results (python regular expressions)
  - ls(\s.*)?
"""
    )


class EventFilter:
    def __init__(self, path: Path):
        self.revision = 0
        self.path = path
        self.signature = None
        self.last_check = -math.inf
        self.min_delay = 5
        self.ignored_commands = set()
        self.boring_patterns = set()
        self.success_return_codes = {0}

    def refresh(self):
        try:
            path = self.path.resolve()
            stat = path.stat()
            signature = (path, stat.st_mtime, stat.st_size)
            if signature == self.signature:
                return

            data = yaml.load(path.read_text(), yaml.Loader)
            self.signature = signature

            # TODO react nicely to broken data
            assert data["format"] == "osh-event-filters-v1", data["format"]
            data.pop("format")
            ignored_commands = set(data.pop("ignore-commands", []))
            boring_patterns = {re.compile(p) for p in data.pop("ignore-patterns", [])}

            # TODO separate config file or more generic file (different name) when we also have this stuff in here?
            success_return_codes = set(data.pop("success-return-codes", [0]))

            assert len(data) == 0, data

        except FileNotFoundError:
            # TODO should we write an empty one if it is not there?
            ignored_commands = set()
            boring_patterns = set()

        if (
            self.ignored_commands == ignored_commands
            and self.boring_patterns == boring_patterns
            and self.success_return_codes == success_return_codes
        ):
            return

        self.revision += 1
        self.ignored_commands = ignored_commands
        self.boring_patterns = boring_patterns
        self.success_return_codes = success_return_codes

    def discard(self, event) -> bool:
        if event.command in self.ignored_commands:
            return True
        for pattern in self.boring_patterns:
            if pattern.fullmatch(event.command):
                return True
        return False
