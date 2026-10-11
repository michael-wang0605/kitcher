from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from kitcher import server
from pantry.models import InventoryEvent


class LocalServerTests(SimpleTestCase):
    def test_regular_server_is_loopback_and_trusts_only_the_local_proxy(self):
        with (
            patch.object(server, "get_wsgi_application", return_value="app"),
            patch.object(server, "serve") as serve,
        ):
            server.main()
        self.assertEqual(serve.call_args.args, ("app",))
        options = serve.call_args.kwargs
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 8001)
        self.assertEqual(options["trusted_proxy"], "127.0.0.1")
        self.assertEqual(options["trusted_proxy_headers"], {"x-forwarded-proto"})
        self.assertTrue(options["clear_untrusted_proxy_headers"])
        self.assertFalse(options["expose_tracebacks"])
        self.assertEqual(options["max_request_body_size"], 11 * 1024 * 1024)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    CSRF_TRUSTED_ORIGINS=["https://testserver"],
)
class HttpsInterfaceTests(TestCase):
    def test_https_login_secure_cookies_and_csrf_protection(self):
        get_user_model().objects.create_user(
            "https-owner", password="test-https-password"
        )
        client = Client(enforce_csrf_checks=True)
        page = client.get(reverse("login"), secure=True)
        self.assertEqual(page.status_code, 200)
        csrf = page.cookies["csrftoken"]
        self.assertTrue(csrf["secure"])
        response = client.post(
            reverse("login"),
            {
                "username": "https-owner",
                "password": "test-https-password",
                "csrfmiddlewaretoken": csrf.value,
            },
            secure=True,
            HTTP_ORIGIN="https://testserver",
            HTTP_REFERER="https://testserver/login/",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.cookies["sessionid"]["secure"])
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertEqual(client.get(reverse("pantry"), secure=True).status_code, 200)
        self.assertEqual(client.get(reverse("pantry")).status_code, 301)
        self.assertEqual(
            client.post(
                reverse("confirm"), {}, secure=True, HTTP_ORIGIN="https://testserver"
            ).status_code,
            403,
        )
        self.assertEqual(InventoryEvent.objects.count(), 0)
