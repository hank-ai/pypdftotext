"""Tests for AzureDocIntelIntegrator and its module-level helpers."""

import os
import threading
import unittest
import warnings
from unittest.mock import MagicMock, patch

from pypdftotext import PyPdfToTextConfig
from pypdftotext.azure_docintel_integrator import (
    AzureDocIntelIntegrator,
    AZURE_READ,
    client_for,
    _client_cache,
    _client_cache_lock,
)


class TestClientFor(unittest.TestCase):
    def setUp(self):
        # Reset the cache between tests so they're hermetic.
        with _client_cache_lock:
            _client_cache.clear()

    def _config_with_creds(self, endpoint, key, pool_maxsize=20):
        return PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": endpoint,
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": key,
            "AZURE_CLIENT_POOL_MAXSIZE": pool_maxsize,
        })

    def test_client_for(self):
        """Parametrized: caching, missing creds, pool size kwarg."""
        # Clear environment AZURE_* vars so config values take precedence.
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)

            # 1. Missing creds → None.
            self.assertIsNone(client_for(self._config_with_creds("", "")))
            self.assertIsNone(client_for(self._config_with_creds("https://x.example", "")))
            self.assertIsNone(client_for(self._config_with_creds("", "key123")))

            # 2. Same (endpoint, key) → same client object.
            cfg_a1 = self._config_with_creds("https://a.example", "key-a")
            cfg_a2 = self._config_with_creds("https://a.example", "key-a")
            client_a1 = client_for(cfg_a1)
            client_a2 = client_for(cfg_a2)
            self.assertIsNotNone(client_a1)
            self.assertIs(client_a1, client_a2)

            # 3. Different key → different client.
            cfg_b = self._config_with_creds("https://a.example", "key-b")
            client_b = client_for(cfg_b)
            self.assertIsNot(client_a1, client_b)

            # 4. Different endpoint → different client.
            cfg_c = self._config_with_creds("https://c.example", "key-a")
            client_c = client_for(cfg_c)
            self.assertIsNot(client_a1, client_c)

            # 5. Pool size kwarg forwarded.
            cfg_pool = self._config_with_creds(
                "https://pool.example", "key-pool", pool_maxsize=42,
            )
            # Patch the SDK constructor to capture the transport kwarg.
            with patch(
                "pypdftotext.azure_docintel_integrator.DocumentIntelligenceClient"
            ) as mock_client_cls, patch(
                "pypdftotext.azure_docintel_integrator.RequestsTransport"
            ) as mock_transport_cls:
                mock_transport_cls.return_value = MagicMock(name="transport")
                mock_client_cls.return_value = MagicMock(name="client")
                client_for(cfg_pool)
                # RequestsTransport called with pool_maxsize=42.
                kwargs = mock_transport_cls.call_args.kwargs
                self.assertEqual(kwargs.get("connection_pool_maxsize"), 42)


class TestSubmit(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()

    def test_submit_returns_poller_when_client_available(self):
        """Happy path: submit returns a poller from the SDK."""
        cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })
        fake_poller = MagicMock(name="poller")
        fake_client = MagicMock(name="client")
        fake_client.begin_analyze_document.return_value = fake_poller
        # Patch the env-var precedence path so config creds take effect.
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)
            with patch(
                "pypdftotext.azure_docintel_integrator.client_for",
                return_value=fake_client,
            ):
                integrator = AzureDocIntelIntegrator(cfg)
                poller = integrator.submit(b"pdfbytes", [0, 2], pdf_name="x.pdf")
        self.assertIs(poller, fake_poller)
        # Verify begin_analyze_document was invoked with the expected pages list
        # (1-based, comma-joined) and the model from config.
        kwargs = fake_client.begin_analyze_document.call_args.kwargs
        self.assertEqual(kwargs["model_id"], cfg.AZURE_DOCINTEL_MODEL)
        self.assertEqual(kwargs["pages"], "1,3")
        # polling kwarg should be a CancellablePolling instance.
        from pypdftotext._cancellable_polling import CancellablePolling
        self.assertIsInstance(kwargs["polling"], CancellablePolling)

    def test_submit_returns_none_when_no_client(self):
        """No creds → submit returns None and logs an error."""
        cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "",
        })
        with patch.dict(os.environ, {}, clear=False):
            for var in ("AZURE_DOCINTEL_ENDPOINT", "AZURE_DOCINTEL_SUBSCRIPTION_KEY"):
                os.environ.pop(var, None)
            integrator = AzureDocIntelIntegrator(cfg)
            with self.assertLogs(
                "pypdftotext.azure_docintel_integrator", level="ERROR"
            ) as captured:
                result = integrator.submit(b"pdf", [0])
        self.assertIsNone(result)
        self.assertTrue(
            any("no client" in line.lower() for line in captured.output),
            f"expected 'no client' in logs; got {captured.output!r}",
        )


class TestAwaitOne(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
            "AZURE_DOCINTEL_TIMEOUT": 30,
        })

    def _populated_analyze_result(self, num_pages=1):
        from azure.ai.documentintelligence.models import AnalyzeResult
        return AnalyzeResult({
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "stringIndexType": "textElements",
            "content": " ".join(f"page{i+1}" for i in range(num_pages)),
            "pages": [
                {"pageNumber": i + 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                 "unit": "inch", "spans": [{"offset": i * 6, "length": 5}],
                 "words": [], "lines": [], "selectionMarks": []}
                for i in range(num_pages)
            ],
            "styles": [],
        })

    def test_await_one_success(self):
        """Happy path: poller returns AnalyzeResult → OCRResult.succeeded."""
        raw = self._populated_analyze_result(num_pages=2)
        poller = MagicMock(name="poller")
        poller.result.return_value = raw
        integrator = AzureDocIntelIntegrator(self.cfg)
        result = integrator.await_one(poller, pdf_name="x.pdf")
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.pages), 2)
        self.assertIs(result.raw, raw)
        self.assertIsNone(result.error)

    def test_await_one_timeout_yields_error_result(self):
        """REGRESSION: poller.result(timeout) returns None on timeout. Must
        produce OCRResult(error="OCR timeout: ...") and not crash."""
        poller = MagicMock(name="poller")
        poller.result.return_value = None
        # Provide a polling method so cancel_event.set() doesn't crash.
        poller._polling_method = MagicMock()
        poller._polling_method.cancel_event = threading.Event()
        integrator = AzureDocIntelIntegrator(self.cfg)
        result = integrator.await_one(poller, pdf_name="x.pdf")
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.raw)
        self.assertEqual(result.pages, [])
        self.assertIsNotNone(result.error)
        self.assertTrue(result.error.startswith("OCR timeout"))
        # The cancel_event should have been set so the daemon poll thread can exit.
        self.assertTrue(poller._polling_method.cancel_event.is_set())

    def test_await_one_error_paths(self):
        """Parametrized: HttpResponseError and empty-pages produce OCRResult.error."""
        from azure.core.exceptions import HttpResponseError
        integrator = AzureDocIntelIntegrator(self.cfg)

        # 1. Poller raises HttpResponseError.
        poller_http = MagicMock(name="poller_http")
        poller_http.result.side_effect = HttpResponseError("server error")
        poller_http._polling_method = MagicMock()
        poller_http._polling_method.cancel_event = threading.Event()
        result_http = integrator.await_one(poller_http, pdf_name="x.pdf")
        self.assertFalse(result_http.succeeded)
        self.assertTrue(result_http.error.startswith("OCR failed"))
        self.assertIn("HttpResponseError", result_http.error)

        # 2. AnalyzeResult with zero pages.
        from azure.ai.documentintelligence.models import AnalyzeResult
        empty_result = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements", "content": "",
            "pages": [], "styles": [],
        })
        poller_empty = MagicMock(name="poller_empty")
        poller_empty.result.return_value = empty_result
        result_empty = integrator.await_one(poller_empty, pdf_name="x.pdf")
        self.assertFalse(result_empty.succeeded)
        self.assertEqual(result_empty.error, "OCR failed: empty result (analyzeResult.pages was empty)")


class TestDeprecatedSurface(unittest.TestCase):
    def setUp(self):
        with _client_cache_lock:
            _client_cache.clear()
        self.cfg = PyPdfToTextConfig(overrides={
            "AZURE_DOCINTEL_ENDPOINT": "https://x.example",
            "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "key",
        })

    def _run_one_ocr(self, integrator):
        """Helper: run a fake successful OCR so thread-local is populated."""
        from azure.ai.documentintelligence.models import AnalyzeResult
        raw = AnalyzeResult({
            "apiVersion": "2024-11-30", "modelId": "prebuilt-read",
            "stringIndexType": "textElements", "content": "page1",
            "pages": [{"pageNumber": 1, "angle": 0.0, "width": 8.5, "height": 11.0,
                       "unit": "inch", "spans": [{"offset": 0, "length": 5}],
                       "words": [], "lines": [], "selectionMarks": []}],
            "styles": [],
        })
        poller = MagicMock()
        poller.result.return_value = raw
        return integrator.await_one(poller, pdf_name="x.pdf"), raw

    def test_last_result_emits_deprecation_warning(self):
        integrator = AzureDocIntelIntegrator(self.cfg)
        _, raw = self._run_one_ocr(integrator)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = integrator.last_result
        self.assertEqual(len(caught), 1)
        self.assertEqual(caught[0].category, DeprecationWarning)
        self.assertIs(value, raw)

    def test_last_result_default_when_no_ocr_on_thread(self):
        """Accessing last_result before any OCR on this thread returns the
        empty AnalyzeResult sentinel (back-compat with the original init)."""
        from azure.ai.documentintelligence.models import AnalyzeResult
        integrator = AzureDocIntelIntegrator(self.cfg)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            value = integrator.last_result
        self.assertIsInstance(value, AnalyzeResult)
        self.assertIsNone(value.pages)  # AnalyzeResult({}).pages is None.


if __name__ == "__main__":
    unittest.main()
