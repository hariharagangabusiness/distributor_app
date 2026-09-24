"""Regression tests for /login rate-limiting - previously nothing stopped
unlimited password guesses against a known username, or unlimited username
guessing from one IP.
"""
from werkzeug.security import generate_password_hash

from tests.base import DBTestCase, appmod, db


class LoginRateLimitTests(DBTestCase):
    def setUp(self):
        super().setUp()
        appmod._login_failures.clear()

    def tearDown(self):
        appmod._login_failures.clear()
        super().tearDown()

    def make_user(self, username="alice", password="CorrectHorse1!"):
        db.execute("INSERT INTO Users (Username, PasswordHash, Role, Active) VALUES (?, ?, 'Admin', 1)",
                   (username, generate_password_hash(password)))
        return username, password

    def test_lockout_after_max_failed_attempts_by_username(self):
        username, password = self.make_user()
        client = appmod.app.test_client()

        for _ in range(appmod.LOGIN_MAX_ATTEMPTS):
            resp = client.post("/login", data={"username": username, "password": "wrong"}, follow_redirects=True)
            self.assertIn(b"Incorrect username or password", resp.data)

        # Even the CORRECT password is now rejected - the account is locked, not just the bad guesses.
        resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        self.assertIn(b"Too many failed login attempts", resp.data)
        self.assertNotIn(b"Welcome back", resp.data)

    def test_successful_login_clears_the_failure_count(self):
        username, password = self.make_user()
        client = appmod.app.test_client()

        for _ in range(appmod.LOGIN_MAX_ATTEMPTS - 1):
            client.post("/login", data={"username": username, "password": "wrong"}, follow_redirects=True)

        resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        self.assertIn(b"Welcome back", resp.data)

        client.post("/logout", follow_redirects=True)
        # One fresh wrong guess right after a successful login should NOT be locked out -
        # the earlier near-miss streak must have been cleared by the success.
        resp = client.post("/login", data={"username": username, "password": "wrong"}, follow_redirects=True)
        self.assertIn(b"Incorrect username or password", resp.data)
        self.assertNotIn(b"Too many failed login attempts", resp.data)

    def test_ip_based_lockout_blocks_username_guessing_from_one_ip(self):
        username, password = self.make_user()
        client = appmod.app.test_client()

        for i in range(appmod.LOGIN_MAX_ATTEMPTS):
            client.post("/login", data={"username": f"nonexistent_user_{i}", "password": "wrong"},
                        follow_redirects=True)

        # Same IP (the test client's default), now trying the real account with the
        # correct password - still blocked, because the IP itself is rate-limited.
        resp = client.post("/login", data={"username": username, "password": password}, follow_redirects=True)
        self.assertIn(b"Too many failed login attempts", resp.data)
        self.assertNotIn(b"Welcome back", resp.data)

    def test_different_ip_is_not_affected_by_another_ips_lockout(self):
        username, password = self.make_user()
        client = appmod.app.test_client()

        for _ in range(appmod.LOGIN_MAX_ATTEMPTS):
            client.post("/login", data={"username": username, "password": "wrong"},
                        environ_overrides={"REMOTE_ADDR": "10.0.0.1"}, follow_redirects=True)

        # Different source IP, same username+correct password - must still work,
        # since IP-based limiting for 10.0.0.1 shouldn't block 10.0.0.2, and the
        # username itself is locked only from repeated attempts (which happened
        # here too) - so use a fresh username to isolate the IP check.
        username2, password2 = self.make_user("bob", "AnotherPass9!")
        resp = client.post("/login", data={"username": username2, "password": password2},
                            environ_overrides={"REMOTE_ADDR": "10.0.0.2"}, follow_redirects=True)
        self.assertIn(b"Welcome back", resp.data)
