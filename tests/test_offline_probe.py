"""Offline connectivity-report checks; no external connections are made."""

import contextlib
import io
import json
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase


class OfflineProbeTests(SimpleTestCase):
    def setUp(self):
        self.probe = runpy.run_path(
            str(settings.BASE_DIR / "scripts" / "offline-probe.py")
        )

    def trial(self, expected, ipv4, ipv6, loopback=True):
        main = self.probe["main"]
        local = Mock()
        if loopback:
            local.open.return_value.__enter__ = Mock(
                return_value=Mock(read=lambda _: b'{"version":"0.40.0"}')
            )
            local.open.return_value.__exit__ = Mock(return_value=False)
        else:
            local.open.side_effect = OSError("Local service unavailable")
        outcomes = iter([ipv4, ipv6])
        with TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            with (
                patch.dict(
                    main.__globals__,
                    {"connect": lambda *args: {"connected": next(outcomes)}},
                ),
                patch(
                    "sys.argv",
                    ["offline-probe", "--expect", expected, "--report", str(report)],
                ),
                patch("urllib.request.build_opener", return_value=local),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main()
            return code, json.loads(report.read_text(encoding="utf-8"))

    def test_blocked_requires_both_external_address_families_to_fail(self):
        code, report = self.trial("blocked", False, True)
        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        code, report = self.trial("blocked", False, False)
        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])

    def test_loopback_must_remain_reachable(self):
        code, report = self.trial("blocked", False, False, loopback=False)
        self.assertEqual(code, 1)
        self.assertFalse(report["loopback"]["reachable"])

    def test_normal_connectivity_control_is_recorded_separately(self):
        code, report = self.trial("allowed", True, True)
        self.assertEqual(code, 0)
        self.assertEqual(report["expected"], "allowed")
        self.assertTrue(report["ipv4"]["connected"])
        self.assertTrue(report["ipv6"]["connected"])

    def test_os_access_denial_is_preserved_as_evidence(self):
        connection = Mock()
        connection.connect.side_effect = PermissionError(13, "Denied")
        fake_socket = Mock()
        fake_socket.__enter__ = Mock(return_value=connection)
        fake_socket.__exit__ = Mock(return_value=False)
        with patch("socket.socket", return_value=fake_socket):
            report = self.probe["connect"]("1.1.1.1", 2)
        self.assertFalse(report["connected"])
        self.assertEqual(report["error"], "PermissionError")
        self.assertEqual(report["errno"], 13)
