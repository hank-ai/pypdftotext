"""Test configuration inheritance behaviors for PyPdfToTextConfig."""

import os
from unittest import mock

import pytest

from pypdftotext._config import PyPdfToTextConfig, PyPdfToTextConfigOverrides


class TestPyPdfToTextConfig:
    """Test suite for PyPdfToTextConfig dataclass."""

    @pytest.mark.unit
    def test_environment_variable_initialization(self):
        """Test that config reads from environment variables on initialization."""
        with mock.patch.dict(
            os.environ,
            {
                "AZURE_DOCINTEL_ENDPOINT": "https://test.endpoint.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "test-key-123",
                "AWS_ACCESS_KEY_ID": "aws-access-key",
                "AWS_SECRET_ACCESS_KEY": "aws-secret-key",
                "AWS_SESSION_TOKEN": "aws-session-token",
            },
        ):
            config = PyPdfToTextConfig()
            assert config.AZURE_DOCINTEL_ENDPOINT == "https://test.endpoint.com"
            assert config.AZURE_DOCINTEL_SUBSCRIPTION_KEY == "test-key-123"
            assert config.AWS_ACCESS_KEY_ID == "aws-access-key"
            assert config.AWS_SECRET_ACCESS_KEY == "aws-secret-key"
            assert config.AWS_SESSION_TOKEN == "aws-session-token"

    @pytest.mark.unit
    def test_environment_base_overrides_precedence(self):
        """Test precedence: overrides > base > environment > defaults."""
        with mock.patch.dict(
            os.environ,
            {
                "AZURE_DOCINTEL_ENDPOINT": "https://env.endpoint.com",
                "AZURE_DOCINTEL_SUBSCRIPTION_KEY": "env-key",
            },
        ):
            # Base config with some values
            base_config = PyPdfToTextConfig()
            base_config.AZURE_DOCINTEL_ENDPOINT = "https://base.endpoint.com"
            base_config.MIN_LINES_OCR_TRIGGER = 5

            # Overrides with conflicting values
            overrides = PyPdfToTextConfigOverrides(
                AZURE_DOCINTEL_ENDPOINT="https://override.endpoint.com", DISABLE_OCR=True
            )

            config = PyPdfToTextConfig(base=base_config, overrides=overrides)

            # Override wins over base and env
            assert config.AZURE_DOCINTEL_ENDPOINT == "https://override.endpoint.com"

            # Base wins over default
            assert config.MIN_LINES_OCR_TRIGGER == 5

            # Override sets new value
            assert config.DISABLE_OCR is True

            # Base inherits from env when not overridden itself
            assert base_config.AZURE_DOCINTEL_SUBSCRIPTION_KEY == "env-key"

    @pytest.mark.unit
    def test_nested_inheritance(self):
        """Test that configs can be chained through multiple inheritance levels."""
        # Level 1: Base config
        config1 = PyPdfToTextConfig()
        config1.MIN_LINES_OCR_TRIGGER = 3
        config1.DISABLE_OCR = True

        # Level 2: Derived from config1
        config2 = PyPdfToTextConfig(base=config1)
        config2.TRIGGER_OCR_PAGE_RATIO = 0.6

        # Level 3: Derived from config2 with overrides
        config3 = PyPdfToTextConfig(base=config2, overrides={"MIN_LINES_OCR_TRIGGER": 7})

        # Verify inheritance chain
        assert config3.MIN_LINES_OCR_TRIGGER == 7  # Override
        assert config3.TRIGGER_OCR_PAGE_RATIO == 0.6  # From config2
        assert config3.DISABLE_OCR is True  # From config1

    @pytest.mark.unit
    def test_invalid_field_in_overrides(self):
        """Test that invalid fields in overrides are not set as attributes."""
        overrides = {
            "INVALID_FIELD": "This should not exist",
            "MIN_LINES_OCR_TRIGGER": 20,  # Valid field
        }

        config = PyPdfToTextConfig(overrides=overrides)

        # Valid override should work
        assert config.MIN_LINES_OCR_TRIGGER == 20

        # Invalid field isn't set as attribute
        assert not hasattr(config, "INVALID_FIELD")
