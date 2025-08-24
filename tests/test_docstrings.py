"""Test module for running doctests in pypdftotext package."""

import doctest
import pkgutil
import unittest
from typing import Any
from types import ModuleType

import pypdftotext


def load_tests(
    loader: unittest.TestLoader | None, tests: unittest.TestSuite, ignore: Any
) -> unittest.TestSuite:
    """
    Load all doctests from pypdftotext modules.

    This function is called automatically by unittest test discovery.
    It walks through all modules in the pypdftotext package and adds
    their doctests to the test suite.

    Args:
        loader: The test loader (unused but required by unittest protocol)
        tests: The test suite to add doctests to
        ignore: Pattern for test discovery (unused but required by unittest protocol)

    Returns:
        The test suite with doctests added

    Note:
        The loader and ignore parameters are part of the unittest load_tests protocol
        and must be present even though they're not used in this implementation.
    """
    # Add doctests from the main pypdftotext module
    tests.addTests(doctest.DocTestSuite(pypdftotext, optionflags=doctest.NORMALIZE_WHITESPACE))

    # Walk through all submodules in pypdftotext
    for _, modname, _ in pkgutil.walk_packages(
        path=pypdftotext.__path__, prefix=pypdftotext.__name__ + ".", onerror=lambda x: None
    ):
        try:
            # Import the module
            module: ModuleType = __import__(modname, fromlist=[""])

            # Try to add doctests from this module
            try:
                suite = doctest.DocTestSuite(module, optionflags=doctest.NORMALIZE_WHITESPACE)
                tests.addTests(suite)
            except ValueError:
                # No doctests in this module
                pass

        except ImportError:
            # Skip modules that can't be imported
            pass

    return tests


class DocstringTests(unittest.TestCase):
    """
    Test case for running doctests in pypdftotext package.

    This class provides an alternative way to run doctests using pytest.
    Each method discovers and runs doctests from specific modules.
    """

    def test_layout_module_doctests(self):
        """Test all doctests in the layout module."""
        import pypdftotext.layout

        results = doctest.testmod(
            pypdftotext.layout, optionflags=doctest.NORMALIZE_WHITESPACE, verbose=False
        )

        self.assertEqual(results.failed, 0, f"Doctests failed in layout module: {results}")

    def test_main_module_doctests(self):
        """Test all doctests in the main pypdftotext module."""
        results = doctest.testmod(
            pypdftotext, optionflags=doctest.NORMALIZE_WHITESPACE, verbose=False
        )

        self.assertEqual(results.failed, 0, f"Doctests failed in main module: {results}")

    def test_pdf_extract_module_doctests(self):
        """Test all doctests in the pdf_extract module."""
        import pypdftotext.pdf_extract

        results = doctest.testmod(
            pypdftotext.pdf_extract, optionflags=doctest.NORMALIZE_WHITESPACE, verbose=False
        )

        self.assertEqual(results.failed, 0, f"Doctests failed in pdf_extract module: {results}")

    def test_azure_module_doctests(self):
        """Test all doctests in the azure_docintel_integrator module."""
        import pypdftotext.azure_docintel_integrator

        results = doctest.testmod(
            pypdftotext.azure_docintel_integrator,
            optionflags=doctest.NORMALIZE_WHITESPACE,
            verbose=False,
        )

        self.assertEqual(
            results.failed, 0, f"Doctests failed in azure_docintel_integrator module: {results}"
        )

    def test_config_module_doctests(self):
        """Test all doctests in the _config module."""
        import pypdftotext._config

        results = doctest.testmod(
            pypdftotext._config, optionflags=doctest.NORMALIZE_WHITESPACE, verbose=False
        )

        self.assertEqual(results.failed, 0, f"Doctests failed in _config module: {results}")


if __name__ == "__main__":
    # Run doctests when executed directly
    import sys

    # Create a test suite with all doctests
    suite = unittest.TestSuite()

    # Add doctests using the load_tests function
    suite = load_tests(None, suite, None)

    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with error code if tests failed
    sys.exit(0 if result.wasSuccessful() else 1)
