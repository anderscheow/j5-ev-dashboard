#!/usr/bin/env python3
"""CarLinko control capability recovery checks, without vendor network access."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(__file__))
import auth
import server


class ControlCapsTest(unittest.TestCase):
    def setUp(self):
        self.cache_patch = patch.object(server, "_VEH_CACHE", {"t": 0.0, "v": None})
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

        self.token_patch = patch.object(server, "_read_token", return_value="stale-token")
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

        self.base_patch = patch.object(auth, "api_base", return_value="https://example.invalid")
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)

        self.headers_patch = patch.object(auth, "headers_for", side_effect=lambda _, token: {"token": token})
        self.headers_patch.start()
        self.addCleanup(self.headers_patch.stop)

        self.login_patch = patch.object(auth, "login", return_value="fresh-token")
        self.login = self.login_patch.start()
        self.addCleanup(self.login_patch.stop)

    def test_empty_vehicle_response_refreshes_token_and_restores_buttons(self):
        replies = [
            {"code": "0000", "data": []},
            {"code": "0000", "data": [{"vehicleControlConfig": '{"Lock": true, "Search": true}'}]},
        ]
        with patch("requests.get", side_effect=[Mock(json=Mock(return_value=reply)) for reply in replies]) as get:
            caps = server.control_caps()
            self.assertTrue(server.control_caps()["lock"])

        self.assertTrue(caps["lock"])
        self.assertTrue(caps["find"])
        self.login.assert_called_once_with()
        self.assertEqual([call.kwargs["headers"]["token"] for call in get.call_args_list],
                         ["stale-token", "fresh-token"])
        self.assertEqual(get.call_count, 2)  # the good response was cached

    def test_valid_unsupported_controls_do_not_trigger_login(self):
        reply = {"code": "0000", "data": [{"vehicleControlConfig": {"Lock": False, "Search": False}}]}
        with patch("requests.get", return_value=Mock(json=Mock(return_value=reply))) as get:
            caps = server.control_caps()

        self.assertFalse(caps["lock"])
        self.assertFalse(caps["find"])
        self.login.assert_not_called()
        get.assert_called_once()

    def test_missing_config_after_refresh_returns_error_and_is_not_cached(self):
        reply = {"code": "0000", "data": []}
        with patch("requests.get", return_value=Mock(json=Mock(return_value=reply))) as get:
            with self.assertRaisesRegex(RuntimeError, "vehicle controls unavailable"):
                server.control_caps()
            with self.assertRaisesRegex(RuntimeError, "vehicle controls unavailable"):
                server.control_caps()

        self.assertIsNone(server._VEH_CACHE["v"])
        self.assertEqual(get.call_count, 4)
        self.assertEqual(self.login.call_count, 2)

    def test_non_success_response_refreshes_token(self):
        replies = [
            {"code": "401", "data": None},
            {"code": "0000", "data": [{"vehicleControlConfig": {"Lock": True}}]},
        ]
        with patch("requests.get", side_effect=[Mock(json=Mock(return_value=reply)) for reply in replies]):
            self.assertTrue(server.control_caps()["lock"])
        self.login.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
