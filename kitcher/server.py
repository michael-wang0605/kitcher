"""Regular local WSGI server; HTTPS/static files are provided by Caddy."""

import os

from django.core.wsgi import get_wsgi_application
from waitress import serve

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kitcher.settings")


def main():
    serve(
        get_wsgi_application(),
        host="127.0.0.1",
        port=8001,
        threads=4,
        trusted_proxy="127.0.0.1",
        trusted_proxy_count=1,
        trusted_proxy_headers={"x-forwarded-proto"},
        clear_untrusted_proxy_headers=True,
        max_request_body_size=11 * 1024 * 1024,
        expose_tracebacks=False,
    )


if __name__ == "__main__":
    main()
