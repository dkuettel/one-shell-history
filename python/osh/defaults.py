import os
from pathlib import Path

dot = Path(os.environ.get("OSH_HOME", "~/.osh")).expanduser()

# the below are all relative to dot
# or whatever the user chose as a base path

archive = Path("archive")
active = Path("active")
event_filters = Path("event-filters.yaml")
local = Path("local.osh")
socket = Path("service.socket")
