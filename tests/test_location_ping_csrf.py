import re
import unittest

from tests.base import DBTestCase, appmod


class LocationPingCsrfTests(DBTestCase):
    def setUp(self):
        super().setUp()
        appmod.app.config["WTF_CSRF_ENABLED"] = True  # base.py turns it off globally
        self.client = appmod.app.test_client()

    def tearDown(self):
        appmod.app.config["WTF_CSRF_ENABLED"] = False
        super().tearDown()

    def test_ping_without_token_is_rejected(self):
        r = self.client.post("/api/location-ping", json={"lat": 1, "lng": 2})
        self.assertEqual(r.status_code, 400)

    def test_ping_with_header_token_passes_csrf(self):
        # Token comes from the login page's <meta name="csrf-token"> / hidden field,
        # same session cookie; past CSRF, require_login() redirects the anonymous client.
        page = self.client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        r = self.client.post("/api/location-ping", json={"lat": 1, "lng": 2},
                             headers={"X-CSRFToken": token})
        self.assertNotEqual(r.status_code, 400)

    def test_token_time_limit_disabled(self):
        self.assertIsNone(appmod.app.config["WTF_CSRF_TIME_LIMIT"])


if __name__ == "__main__":
    unittest.main()
