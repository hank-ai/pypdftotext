"""Tests for the module-level await_all collective wait helper."""

import threading
import time
import unittest
from unittest.mock import MagicMock

from pypdftotext import PyPdfToTextConfig
from pypdftotext.azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    await_all,
    iter_results,
)


def _fake_poller(immediate_result=None, raises=None, fire_callbacks=True):
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

    if fire_callbacks:
        poller.add_done_callback.side_effect = _add_done_callback
    else:
        # Simulate a still-running LRO: callbacks are only stored. A test can
        # fire them later with ``for fn in poller._stored_callbacks: fn(poller)``.
        poller._stored_callbacks = []
        poller.add_done_callback.side_effect = poller._stored_callbacks.append

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
        self.assertEqual(
            results["bravo"].error, "OCR batch budget exceeded after 0.5s (pdf still pending)"
        )
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


class TestIterResults(unittest.TestCase):
    def setUp(self):
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        self.integrator = AzureDocIntelIntegrator(self.cfg)

    def test_iter_results_yields_in_completion_order_and_consumes_pollers(self):
        pollers = {"a": _fake_poller(immediate_result=_make_analyze_result("a")),
                   "b": _fake_poller(immediate_result=_make_analyze_result("b"))}
        out = list(iter_results(pollers, self.integrator, config=self.cfg))
        self.assertEqual({n for n, _ in out}, {"a", "b"})
        self.assertTrue(all(r.succeeded for _, r in out))
        self.assertEqual(pollers, {})  # consumed

    def test_iter_results_releases_poller_before_yield(self):
        pollers = {"a": _fake_poller(immediate_result=_make_analyze_result("a"))}
        gen = iter_results(pollers, self.integrator, config=self.cfg)
        name, _ = next(gen)
        self.assertEqual(name, "a")
        self.assertNotIn("a", pollers)  # popped before the yield

    def test_iter_results_empty_pollers_yields_nothing(self):
        self.assertEqual(list(iter_results({}, self.integrator, config=self.cfg)), [])

    def test_iter_results_yields_each_name_once_when_callback_fires_twice(self):
        poller = _fake_poller(immediate_result=_make_analyze_result("a"))

        def fire_twice(fn):
            fn(poller)
            fn(poller)

        poller.add_done_callback.side_effect = fire_twice
        out = list(iter_results({"a": poller}, self.integrator, config=self.cfg))
        self.assertEqual([n for n, _ in out], ["a"])

    def test_iter_results_per_pdf_timeout_cancels_and_reports(self):
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
        pending = _fake_poller(immediate_result=None, fire_callbacks=False)  # result() -> None
        t0 = time.monotonic()
        out = dict(iter_results({"slow": pending}, self.integrator, config=cfg))
        self.assertLess(time.monotonic() - t0, 4.0)
        self.assertFalse(out["slow"].succeeded)
        self.assertEqual(
            out["slow"].error, "OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT=1s"
        )
        self.assertTrue(pending._polling_method.cancel_event.is_set())
        pending.result.assert_called_once()
        # _CANCEL_GRACE_SECONDS, minus the microseconds spent since the pass began.
        self.assertAlmostEqual(pending.result.call_args.args[0], 5.0, delta=0.5)

    def test_iter_results_per_pdf_deadline_anchored_at_submit_time(self):
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
        old = _fake_poller(immediate_result=None, fire_callbacks=False)
        fresh = _fake_poller(immediate_result=_make_analyze_result("fresh"), fire_callbacks=False)
        now = time.monotonic()
        gen = iter_results({"old": old, "fresh": fresh}, self.integrator, config=cfg,
                           submitted_at={"old": now - 10.0, "fresh": now})
        name, result = next(gen)  # "old" expired immediately; "fresh" still has ~1s
        self.assertEqual(name, "old")
        self.assertFalse(result.succeeded)
        for fn in fresh._stored_callbacks:  # fresh completes within its own window
            fn(fresh)
        name, result = next(gen)
        self.assertEqual(name, "fresh")
        self.assertTrue(result.succeeded)

    def test_iter_results_batch_timeout_reports_budget_exceeded(self):
        pending = _fake_poller(immediate_result=None, fire_callbacks=False)
        out = dict(iter_results({"p": pending}, self.integrator, config=self.cfg,
                                batch_timeout=0.5))
        self.assertTrue(out["p"].error.startswith("OCR batch budget exceeded after 0.5s"))
        self.assertTrue(pending._polling_method.cancel_event.is_set())

    def test_iter_results_expired_poller_with_late_success_yields_success(self):
        cfg = PyPdfToTextConfig(base=self.cfg, overrides={"AZURE_DOCINTEL_TIMEOUT": 1})
        lucky = _fake_poller(immediate_result=_make_analyze_result("lucky"), fire_callbacks=False)
        out = dict(iter_results({"lucky": lucky}, self.integrator, config=cfg))
        self.assertTrue(out["lucky"].succeeded)  # result() returned the real payload after cancel
        self.assertTrue(lucky._polling_method.cancel_event.is_set())

    def test_iter_results_drops_poller_reference_before_yield(self):
        """While the consumer applies a result, nothing in iter_results may still hold
        the harvested poller (it owns the full HTTP response body and parsed JSON)."""
        import gc
        import weakref

        pollers = {"a": _fake_poller(immediate_result=_make_analyze_result("a"))}
        ref = weakref.ref(pollers["a"])
        gen = iter_results(pollers, self.integrator, config=self.cfg)
        name, result = next(gen)
        self.assertEqual(name, "a")
        self.assertTrue(result.succeeded)
        gc.collect()  # the mock has internal cycles; collect them so only live refs remain
        self.assertIsNone(ref(), "poller still referenced while its result is being consumed")

    def test_iter_results_cancels_all_expired_pollers_before_joining_any(self):
        """Expired pollers wind down concurrently: every cancel_event is set before the
        first grace join, and the joins share one grace budget."""
        first = _fake_poller(immediate_result=None, fire_callbacks=False)
        second = _fake_poller(immediate_result=None, fire_callbacks=False)
        other_cancelled_at_join = {}

        def _first_result(timeout=None):
            other_cancelled_at_join["first"] = second._polling_method.cancel_event.is_set()
            time.sleep(0.2)  # eat into the shared grace budget
            return None

        def _second_result(timeout=None):
            other_cancelled_at_join["second"] = first._polling_method.cancel_event.is_set()
            time.sleep(0.2)
            return None

        first.result.side_effect = _first_result
        second.result.side_effect = _second_result
        now = time.monotonic()
        out = dict(iter_results({"first": first, "second": second}, self.integrator,
                                config=self.cfg,
                                submitted_at={"first": now - 100.0, "second": now - 100.0}))
        self.assertTrue(other_cancelled_at_join["first"])
        self.assertTrue(other_cancelled_at_join["second"])
        self.assertFalse(out["first"].succeeded)
        self.assertFalse(out["second"].succeeded)
        # The second join got what was left of the single 5s budget, not a fresh 5s.
        joins = sorted(p.result.call_args.args[0] for p in (first, second))
        self.assertLess(joins[0], 4.9)
        self.assertLessEqual(joins[1], 5.0)

    def test_iter_results_harvest_exception_yields_error_result_and_continues(self):
        """A non-Azure exception while harvesting one poller becomes that PDF's error
        result; the remaining PDFs are still harvested."""
        bad = _fake_poller(immediate_result=_make_analyze_result("bad"))
        bad.result.side_effect = RuntimeError("renderer exploded")
        good = _fake_poller(immediate_result=_make_analyze_result("good"))
        out = dict(iter_results({"bad": bad, "good": good}, self.integrator, config=self.cfg))
        self.assertFalse(out["bad"].succeeded)
        self.assertEqual(out["bad"].error, "OCR failed: RuntimeError: renderer exploded")
        self.assertTrue(out["good"].succeeded)

    def test_iter_results_expired_poller_keeps_real_error_over_timeout_label(self):
        """When the grace harvest of an expired poller yields a real (non-timeout)
        error, that error is reported, not relabeled as a timeout."""
        from azure.core.exceptions import HttpResponseError

        now = time.monotonic()
        raising = _fake_poller(immediate_result=None, fire_callbacks=False)
        raising.result.side_effect = RuntimeError("renderer exploded")
        azure_failed = _fake_poller(immediate_result=None, fire_callbacks=False)
        azure_failed.result.side_effect = HttpResponseError("server said no")
        silent = _fake_poller(immediate_result=None, fire_callbacks=False)  # result() -> None
        out = dict(iter_results(
            {"raising": raising, "azure_failed": azure_failed, "silent": silent},
            self.integrator, config=self.cfg,
            submitted_at={n: now - 100.0 for n in ("raising", "azure_failed", "silent")},
        ))
        self.assertEqual(out["raising"].error, "OCR failed: RuntimeError: renderer exploded")
        self.assertTrue(out["azure_failed"].error.startswith("OCR failed: HttpResponseError"))
        self.assertEqual(
            out["silent"].error, "OCR timeout: no result within AZURE_DOCINTEL_TIMEOUT=60s"
        )

    def test_iter_results_callback_does_not_call_result(self):
        """Regression: the done-callback must only enqueue; poller.result() runs on the
        coordinator thread. Mirrors TestAwaitAllThreadSafety for iter_results."""
        raw = _make_analyze_result("x")
        poller = _fake_poller(immediate_result=raw, fire_callbacks=False)
        result_thread = {}

        def _result(timeout=None):
            result_thread["ident"] = threading.get_ident()
            return raw

        poller.result.side_effect = _result
        gen = iter_results({"x": poller}, self.integrator, config=self.cfg)
        worker = threading.Thread(target=lambda: [fn(poller) for fn in poller._stored_callbacks])
        worker.start()
        worker.join()
        self.assertNotIn("ident", result_thread)  # callback did not call result()
        name, result = next(gen)
        self.assertTrue(result.succeeded)
        self.assertEqual(result_thread["ident"], threading.get_ident())


if __name__ == "__main__":
    unittest.main()
