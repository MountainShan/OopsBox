"""App-level routes."""
import unittest

from dashboard import main


class TestServiceWorker(unittest.TestCase):
    def test_served_from_the_root_for_the_whole_app(self):
        r = main.service_worker()
        self.assertTrue(str(r.path).endswith("static/sw.js"))
        self.assertEqual(r.headers["service-worker-allowed"], "/")
        self.assertEqual(r.media_type, "text/javascript")


if __name__ == "__main__":
    unittest.main()
