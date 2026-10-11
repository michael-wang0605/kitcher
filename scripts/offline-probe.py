"""Connectivity evidence for a program-scoped offline trial; no photos sent."""

import argparse
import json
import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def connect(address, family):
    started = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect((address, 443))
        return {"connected": True, "seconds": round(time.monotonic() - started, 3)}
    except OSError as exc:
        return {
            "connected": False,
            "seconds": round(time.monotonic() - started, 3),
            "error": type(exc).__name__,
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", choices=["allowed", "blocked"], required=True)
    parser.add_argument("--report", required=True)
    options = parser.parse_args()
    report = {
        "created": datetime.now(timezone.utc).isoformat(),
        "expected": options.expect,
        "method": "TCP connect only to Cloudflare public DNS service addresses on port 443; no source photo or credentials sent",
        "ipv4": connect("1.1.1.1", socket.AF_INET),
        "ipv6": connect("2606:4700:4700::1111", socket.AF_INET6),
    }
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:11435/api/version", timeout=3) as response:
            report["loopback"] = {
                "reachable": True,
                "version": json.loads(response.read(4096))["version"],
            }
    except (OSError, ValueError, KeyError) as exc:
        report["loopback"] = {"reachable": False, "error": type(exc).__name__}
    report["passed"] = report["loopback"]["reachable"] and (
        report["ipv4"]["connected"]
        if options.expect == "allowed"
        else not report["ipv4"]["connected"] and not report["ipv6"]["connected"]
    )
    Path(options.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
