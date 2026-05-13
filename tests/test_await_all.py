"""Tests for the module-level await_all collective wait helper."""

import threading
import unittest
from unittest.mock import MagicMock

from pypdftotext import PyPdfToTextConfig
from pypdftotext.azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    await_all,
)


def _fake_poller(immediate_result=None, raises=None):
    """Build a mocked poller that simulates SDK semantics.

    If immediate_result is set, the poller is treated as already "done":
        - add_done_callback(fn) calls fn(poller) immediately and
          synchronously, mimicking the SDK firing callbacks when the
          background poll detects completion before the caller registers.
        - result(timeout) returns the stored result.

    If raises is set, the poller behaves like the SDK raising on result()
    (e.g., HttpResponseError). Callbacks are still fired immediately.
    """
    poller = MagicMock()
    poller._polling_method = MagicMock()
    poller._polling_method.cancel_event = threading.Event()

    def _add_done_callback(fn):
        # SDK fires this synchronously if the LRO is already complete.
        fn(poller)

    poller.add_done_callback.side_effect = _add_done_callback

    if raises is not None:
        poller.result.side_effect = raises
    else:
        poller.result.return_value = immediate_result
    return poller


def _make_analyze_result(label):
    from azure.ai.documentintelligence.models import AnalyzeResult
    return AnalyzeResult({
        "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
        "stringIndexType": "textElements", "content": label,
        "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                   "unit": "inch", "spans": [{"offset": 0, "length": len(label)}],
                   "words": [], "lines": [], "selectionMarks": []}],
        "styles": [],
    })


class TestAwaitAll(unittest.TestCase):
    def setUp(self):
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        self.integrator = AzureDocIntelIntegrator(self.cfg)

    def test_await_all_collects_all_results(self):
        """N=0, N=1, N=3 all return well-formed dicts."""
        for size in (0, 1, 3):
            with self.subTest(size=size):
                pollers = {
                    f"pdf{i}": _fake_poller(immediate_result=_make_analyze_result(f"pdf{i}"))
                    for i in range(size)
                }
                results = await_all(
                    pollers, self.integrator, timeout=5.0, config=self.cfg,
                )
                self.assertEqual(len(results), size)
                for name in pollers:
                    self.assertTrue(results[name].succeeded)

    def test_await_all_synthesizes_budget_exceeded_on_timeout(self):
        """Pollers that don't complete by the budget get synthesized error
        results; a poller that completes during the grace window wins."""
        # Poller A completes immediately.
        poller_a = _fake_poller(immediate_result=_make_analyze_result("alpha"))

        # Poller B never fires its callback — simulates "still polling".
        poller_b = MagicMock()
        poller_b._polling_method = MagicMock()
        poller_b._polling_method.cancel_event = threading.Event()
        # Don't fire on add_done_callback (just register it).
        poller_b.add_done_callback.side_effect = lambda fn: None
        # If anything ever does call result, return None (the silent-timeout case).
        poller_b.result.return_value = None

        pollers = {"alpha": poller_a, "bravo": poller_b}
        # Short budget so timeout fires fast.
        results = await_all(pollers, self.integrator, timeout=0.5, config=self.cfg)

        self.assertTrue(results["alpha"].succeeded)
        self.assertFalse(results["bravo"].succeeded)
        self.assertTrue(results["bravo"].error.startswith("OCR batch budget exceeded"))
        # Cancellation was attempted on the still-pending poller.
        self.assertTrue(poller_b._polling_method.cancel_event.is_set())


class TestAwaitAllThreadSafety(unittest.TestCase):
    """Regression: callbacks must NOT call poller.result() because they fire
    on the SDK's daemon polling thread, which would self-join."""

    def setUp(self):
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        self.integrator = AzureDocIntelIntegrator(self.cfg)

    def test_callback_does_not_call_result(self):
        """If await_all's callback called poller.result() on the daemon
        thread, real SDK pollers would raise RuntimeError. Verify the
        callback only signals completion — poller.result() is called from
        the coordinating thread later."""
        from azure.ai.documentintelligence.models import AnalyzeResult

        # Construct a poller mock that tracks which thread calls result().
        result_thread = {}
        raw = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements", "content": "x",
            "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5,
                       "height": 11.0, "unit": "inch",
                       "spans": [{"offset": 0, "length": 1}],
                       "words": [], "lines": [], "selectionMarks": []}],
            "styles": [],
        })

        poller = MagicMock()
        poller._polling_method = MagicMock()
        poller._polling_method.cancel_event = threading.Event()

        # Simulate the SDK firing the callback on a DIFFERENT thread (its
        # own daemon thread), not the coordinator thread.
        def fire_on_daemon_thread(fn):
            t = threading.Thread(
                target=fn, args=(poller._polling_method,), daemon=True,
            )
            t.start()
            t.join()  # ensure callback completes before add_done_callback returns

        poller.add_done_callback.side_effect = fire_on_daemon_thread

        def record_thread_and_return(*args, **kwargs):
            result_thread["name"] = threading.current_thread().name
            return raw

        poller.result.side_effect = record_thread_and_return

        coordinator_thread_name = threading.current_thread().name
        results = await_all({"pdf1": poller}, self.integrator, timeout=2.0, config=self.cfg)

        self.assertEqual(len(results), 1)
        self.assertTrue(results["pdf1"].succeeded)
        # poller.result() MUST have been called from the coordinator thread
        # (not the daemon thread that fired the callback). If the callback
        # called result(), result_thread['name'] would be the daemon's name.
        self.assertEqual(result_thread.get("name"), coordinator_thread_name)


if __name__ == "__main__":
    unittest.main()
