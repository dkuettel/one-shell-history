import queue
import threading
import time
from typing import Iterable, Optional

from osh.history import (
    AggregatedEvent,
    Event,
    History as Events,
    LazyHistory,
    aggregate_events_for_search,
)


# TODO renames
# probably make it Osh after all, makes sense, but call instances history?
# then the .history module needs to become data or something?
# lets make this one here the main instance to use, make it lightweight enough then with the threading?
# and the locking?
# or make it decorated for the extra functionality like syncing, or locking?
class History:
    def __init__(
        self,
        history: Events = None,
        sync_interval: Optional[float] = None,
    ):
        self._history = history or LazyHistory()
        self._history_lock = threading.Lock()
        self._sync_queue = queue.Queue()
        self.sync_interval = sync_interval
        self._sync_thread = threading.Thread(target=self._sync_run)
        self._sync_thread.start()

    def _sync_run(self):
        interval = self._sync_queue.get()
        last_sync = time.time()
        while True:
            try:
                while True:
                    if interval is None:
                        timeout = None
                    else:
                        timeout = interval - (time.time() - last_sync)
                    print(f"{timeout=}")
                    interval = self._sync_queue.get(timeout=timeout)
                    if interval == "exit":
                        return
            except queue.Empty:
                pass
            with self._history_lock:
                print("sync because of interval")
                self._history.sync()
            last_sync = time.time()

    @property
    def sync_interval(self):
        return self._sync_interval

    @sync_interval.setter
    def sync_interval(self, value: Optional[float]):
        self._sync_interval = value
        self._sync_queue.put(value)

    def insert_event(self, event: Event):
        with self._history_lock:
            self._history.insert_event(event)
        event_command = event.command.replace("\n", "\\n")
        print(f"insert event {event_command}", flush=True)

    def list_events(self) -> Iterable[AggregatedEvent]:
        with self._history_lock:
            events = self._history.as_list()
        events = aggregate_events_for_search(events)
        return events

    def list_session_backwards(self, session: str) -> Iterable[Event]:
        with self._history_lock:
            events = self._history.as_list()
        events = reversed(events)
        events = (e for e in events if e.session == session)
        return events

    def close(self):
        with self._history_lock:
            self._history.sync()
        self._sync_queue.put("exit")
        self._sync_thread.join()
        self._sync_queue = None
        self._sync_thread = None
