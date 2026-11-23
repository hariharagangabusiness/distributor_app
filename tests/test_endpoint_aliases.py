import unittest

import app as appmod
from blueprints import ENDPOINT_ALIASES


class EndpointAliasTests(unittest.TestCase):
    def test_old_endpoint_names_still_build_to_the_same_url(self):
        from flask import url_for
        with appmod.app.test_request_context():
            for old, new in ENDPOINT_ALIASES.items():
                rule = next(appmod.app.url_map.iter_rules(new))
                kwargs = {arg: 1 for arg in rule.arguments - set(rule.defaults or ())}
                self.assertEqual(url_for(old, **kwargs), url_for(new, **kwargs), old)
            self.assertEqual(url_for("login", next="/x"), "/login?next=/x")

    def test_every_blueprint_route_has_an_alias(self):
        # Each view moved into a blueprint must still be reachable by its old bare endpoint name.
        for rule in appmod.app.url_map.iter_rules():
            if "." in rule.endpoint:
                bare = rule.endpoint.split(".", 1)[1]
                self.assertEqual(ENDPOINT_ALIASES.get(bare), rule.endpoint, rule.endpoint)

    def test_moved_domains_are_registered(self):
        for name in ("auth", "sales", "customers", "purchases", "inventory", "reports_gst", "payroll", "admin"):
            self.assertIn(name, appmod.app.blueprints)


if __name__ == "__main__":
    unittest.main()
