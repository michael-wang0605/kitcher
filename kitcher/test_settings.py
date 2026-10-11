"""HTTP test-client profile, independent of production's required TLS redirect.

Dedicated HTTPS tests override these settings. Production startup always uses
kitcher.settings; this module never changes the real app's security profile.
"""

from kitcher.settings import *  # noqa: F403

HTTPS = False
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = None
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
