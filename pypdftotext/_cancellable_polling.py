"""Internal subclass of LROBasePolling that supports cooperative cancellation.

`LROPoller.result(timeout)` does not raise on timeout; it returns whatever
`_polling_method.resource()` produces given the most recent polled response.
That leaves the SDK's daemon polling thread running indefinitely, even when
the caller no longer cares about the result.

`CancellablePolling` adds a `cancel_event` Event. When set, the next call to
`_delay()` returns immediately and `finished()` reports True, causing the
polling loop in `LROBasePolling._poll()` to exit cleanly. The done callbacks
then fire (possibly capturing a just-completed result; see await_one in
azure_docintel_integrator), and the daemon thread terminates.

Brittleness note: this subclass depends on the private `_delay`,
`_extract_delay`, and `finished` methods of LROBasePolling. The test in
tests/test_cancellable_polling.py is a canary that will fail loudly if those
methods are refactored in a future azure-core version.
"""

from __future__ import annotations

import threading

from azure.core.polling.base_polling import LROBasePolling


class CancellablePolling(LROBasePolling):
    """LROBasePolling with cooperative cancellation via a threading.Event."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_event = threading.Event()

    def _delay(self) -> None:
        """Sleep for the inter-poll delay, returning immediately if cancelled.

        Overrides ``LROBasePolling._delay``, which calls
        ``self._transport.sleep(delay)`` unconditionally. ``Event.wait(timeout)``
        gives us the same blocking behavior but returns early when the event is
        set, enabling cancellation within milliseconds.
        """
        self.cancel_event.wait(self._extract_delay())

    def finished(self) -> bool:
        """True when cancelled OR when the polled operation reports done."""
        if self.cancel_event.is_set():
            return True
        return super().finished()
