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


if __name__ == "__main__":
    unittest.main()
