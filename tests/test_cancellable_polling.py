"""SDK canary test: CancellablePolling depends on private LROBasePolling internals.

If azure-core refactors `_delay`, `_extract_delay`, or `finished` in
LROBasePolling, the construction or behavior here will fail loudly — that's
the point. CI failure here means: go read the azure-core changelog.
"""

import threading
import time
import unittest
from unittest.mock import MagicMock

from pypdftotext._cancellable_polling import CancellablePolling


class TestCancellablePolling(unittest.TestCase):
    def _make_polling(self, lro_delay=0.05):
        """Construct a CancellablePolling with a short polling interval.

        We bypass `initialize()` (which needs a full pipeline response) by
        injecting the attributes `_delay` reads directly. This keeps the test
        focused on the cancel-event semantics rather than the SDK plumbing.
        """
        polling = CancellablePolling(lro_delay)
        # Minimal scaffolding so _extract_delay/_extract_delay don't crash.
        polling._timeout = lro_delay
        polling._pipeline_response = MagicMock()
        polling._pipeline_response.http_response.headers = {}
        return polling

    def test_cancel_event_terminates_polling(self):
        """cancel_event.set() makes _delay return immediately AND finished()
        return True."""
        polling = self._make_polling(lro_delay=10.0)  # Would normally sleep 10s
        # finished() should be False before cancel is set (super().finished()
        # raises without an _operation; we override that).
        self.assertFalse(polling.cancel_event.is_set())

        # Cancel from a sidecar thread; main thread blocks in _delay.
        def cancel_after():
            time.sleep(0.05)
            polling.cancel_event.set()
        threading.Thread(target=cancel_after, daemon=True).start()

        start = time.monotonic()
        polling._delay()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5, "expected _delay to return within 500ms after cancel")
        self.assertTrue(polling.finished())


if __name__ == "__main__":
    unittest.main()
