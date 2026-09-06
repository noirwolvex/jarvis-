import os
import unittest
from unittest.mock import patch

from core.chrome_cdp import _cdp_url


class ChromeCdpTests(unittest.TestCase):
    def test_default_cdp_url(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_CHROME_CDP_URL", None)
            self.assertEqual(_cdp_url(), "http://127.0.0.1:9222")

    def test_custom_cdp_url_is_normalized(self) -> None:
        with patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": "http://127.0.0.1:9333/"}, clear=False):
            self.assertEqual(_cdp_url(), "http://127.0.0.1:9333")


if __name__ == "__main__":
    unittest.main()
