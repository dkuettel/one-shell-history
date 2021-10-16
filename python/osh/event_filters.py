import json
import math
import re
from pathlib import Path

import yaml

from osh.history import Event


class EventFilter:
    def __init__(self, path: Path):
        self.revision = 0
        self.path = path
        self.signature = None
        self.last_check = -math.inf
        self.min_delay = 5
        self.ignored_commands = set()
        self.boring_patterns = set()

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
            assert len(data) == 0, data

        except FileNotFoundError:
            # TODO should we write an empty one if it is not there?
            ignored_commands = set()
            boring_patterns = set()

        if (
            self.ignored_commands == ignored_commands
            and self.boring_patterns == boring_patterns
        ):
            return

        self.revision += 1
        self.ignored_commands = ignored_commands
        self.boring_patterns = boring_patterns

    def discard(self, event: Event) -> bool:
        if event.command in self.ignored_commands:
            return True
        for pattern in self.boring_patterns:
            if pattern.fullmatch(event.command):
                return True
        return False
