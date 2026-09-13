import importlib.util
import unittest
from unittest.mock import patch
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("bluescope_workbench_server", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(server)


class WorkbenchTests(unittest.TestCase):
    def test_classifies_lysaght_candidates(self):
        self.assertEqual(server.classify("C10019")[0], "ZED & CEE Purlins and Girts")
        self.assertEqual(server.classify("CUSTOM-ORB")[0], "CUSTOM ORB roof and wall cladding")
        self.assertIsNone(server.classify("YELLOWTONGUE-19-3600X800"))

    def test_stock_plan_observes_length_and_kerf(self):
        items = [
            {"part_number": "C10019", "length_mm": 2400, "quantity": 3},
            {"part_number": "C10019", "length_mm": 1500, "quantity": 1},
        ]
        plans = server.make_stock_plan(items, stock_length=9000, kerf=3)
        self.assertEqual(plans[0]["stock_bars"], 1)
        self.assertEqual(plans[0]["cut_count"], 4)
        self.assertLessEqual(plans[0]["bars"][0]["used_mm"], 9000)

    def test_probe_reaches_gateway_without_local_key_gate(self):
        error = server.urllib.error.HTTPError(
            url=f"{server.API_BASE}/isAlive",
            code=401,
            msg="Access Denied",
            hdrs=None,
            fp=None,
        )
        with patch.object(server, "SUBSCRIPTION_KEY", ""), patch.object(
            server.urllib.request, "urlopen", side_effect=error
        ) as urlopen:
            status, result = server.probe_bluescope()
        self.assertEqual(status, 200)
        self.assertEqual(result["upstream_status"], 401)
        self.assertIn("active subscription key", result["message"])
        self.assertTrue(urlopen.called)


if __name__ == "__main__":
    unittest.main()
