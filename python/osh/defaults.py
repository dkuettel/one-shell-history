from pathlib import Path

# TODO dot = Path("~/.one-shell-history").expanduser()
dot = Path(".").expanduser()
histories = Path("histories")
event_filters = Path("event-filters.yaml")
local = Path("local.osh")
socket = Path("service.socket")
