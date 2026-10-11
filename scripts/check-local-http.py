"""Read-only HTTPS/auth probes against the configured local app.

Uses the existing private local credential file without printing credentials.
Does not submit a valid inventory change or save fetched private HTML.
"""

import http.cookiejar
import ipaddress
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and "name" in values:
            self.values[values["name"]] = values.get("value", "")


def values(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )


def main():
    configuration = values(ROOT / "var" / "local.env")
    base = configuration["KITCHER_PHONE_URL"].rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    address = ipaddress.ip_address(parsed.hostname)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path
        or not address.is_private
    ):
        raise RuntimeError("Probe must use the configured private HTTPS origin.")
    credentials = values(ROOT / "var" / "initial-account.txt")
    context = ssl.create_default_context(
        cafile=str(
            ROOT / "var" / "caddy-data" / "pki" / "authorities" / "local" / "root.crt"
        )
    )
    cookies = http.cookiejar.CookieJar()

    class LocalRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            if urllib.parse.urlsplit(newurl).netloc != parsed.netloc:
                raise RuntimeError("Local HTTP probe refused an external redirect.")
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(cookies),
        LocalRedirect(),
    )
    with opener.open(base + "/login/", timeout=10) as response:
        form = Inputs()
        form.feed(response.read().decode("utf-8"))
    body = urllib.parse.urlencode(
        {
            "username": credentials["username"],
            "password": credentials["password"],
            "csrfmiddlewaretoken": form.values["csrfmiddlewaretoken"],
        }
    ).encode("ascii")
    request = urllib.request.Request(
        base + "/login/",
        data=body,
        headers={"Origin": base, "Referer": base + "/login/"},
    )
    with opener.open(request, timeout=10) as response:
        if response.url.endswith("/login/"):
            raise RuntimeError(
                "Local login did not establish a session. The private credential file may need updating."
            )
    saved = {cookie.name: cookie for cookie in cookies}
    if (
        not saved.get("sessionid")
        or not saved["sessionid"].secure
        or "HttpOnly" not in saved["sessionid"]._rest
        or not saved["csrftoken"].secure
    ):
        raise RuntimeError("HTTPS cookie flags did not match production settings.")
    statuses = {}
    for path in (
        "/",
        "/ingredients/",
        "/recipes/",
        "/shopping/",
        "/imports/",
        "/history/",
        "/status/",
        "/static/pantry/app.css",
    ):
        with opener.open(base + path, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Local page did not load: " + path)
            statuses[path] = response.status
    try:
        opener.open(
            urllib.request.Request(
                base + "/inventory/confirm/", data=b"", headers={"Origin": base}
            ),
            timeout=10,
        )
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
    else:
        raise RuntimeError("A confirmation without CSRF was not refused.")
    with opener.open(
        urllib.request.Request(
            base + "/status/", headers={"X-Forwarded-Proto": "http"}
        ),
        timeout=10,
    ) as response:
        if response.status != 200:
            raise RuntimeError("Proxy scheme handling did not match HTTPS.")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin": base,
        "scope": "HTTPS/auth/read-only HTTP probes; not a visual or actual-phone walkthrough",
        "certificate_validation": "passed with the generated public root; no bypass",
        "login": "successful",
        "secure_session_cookie": True,
        "http_only_session_cookie": True,
        "secure_csrf_cookie": True,
        "routes": statuses,
        "missing_csrf_confirmation": 403,
        "browser_scheme_header_overridden": True,
    }
    path = (
        ROOT
        / "var"
        / (
            "http-check-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + ".json"
        )
    )
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
