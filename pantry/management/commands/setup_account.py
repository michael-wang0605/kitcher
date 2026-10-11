"""Create the first account without printing or committing its password."""

import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the local account; save a generated password privately in var/initial-account.txt."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="owner")

    def handle(self, *args, **options):
        username = options["username"].strip()
        if not username:
            raise CommandError("Username cannot be empty.")
        model = get_user_model()
        if model.objects.filter(username=username).exists():
            self.stdout.write(
                "Account already exists; no password or permissions changed."
            )
            return
        password = secrets.token_urlsafe(24)
        path = Path(settings.VAR_DIR) / "initial-account.txt"
        if path.exists():
            raise CommandError(
                "Credential file already exists. Move it before creating another account."
            )
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(f"username={username}\npassword={password}\n")
            model.objects.create_superuser(username=username, password=password)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.stdout.write(
            f"Created account. Credentials saved to {path}; password not printed."
        )
