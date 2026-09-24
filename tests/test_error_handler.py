"""Regression test for the global unhandled-exception handler - item from
the architecture review (no structured logging/error monitoring existed at
all previously)."""
import logging
import io

from tests.base import DBTestCase, appmod


class ErrorHandlerTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self._log_capture = io.StringIO()
        self._handler = logging.StreamHandler(self._log_capture)
        appmod.logger.addHandler(self._handler)
        self._orig_debug = appmod.app.debug
        appmod.app.debug = False  # matches production - the custom handler must fire, not Flask's debugger

    def tearDown(self):
        appmod.logger.removeHandler(self._handler)
        appmod.app.debug = self._orig_debug
        super().tearDown()

    def test_unhandled_exception_is_logged_and_returns_friendly_500(self):
        client = self.make_admin_client()

        orig = appmod.get_effective_columns
        appmod.get_effective_columns = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            resp = client.get("/customers")
        finally:
            appmod.get_effective_columns = orig

        self.assertEqual(resp.status_code, 500)
        self.assertIn(b"Something went wrong", resp.data)
        self.assertNotIn(b"Traceback (most recent call last)", resp.data)
        log_output = self._log_capture.getvalue()
        self.assertIn("boom", log_output)
        self.assertIn("/customers", log_output)
        self.assertIn("user=test_admin", log_output)

    def test_normal_404_is_not_treated_as_a_crash(self):
        client = self.make_admin_client()
        resp = client.get("/this-route-does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("Unhandled exception", self._log_capture.getvalue())
