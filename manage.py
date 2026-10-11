#!/usr/bin/env python
"""Django command entry point."""

import os
import sys

if __name__ == "__main__":
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        "kitcher.test_settings" if sys.argv[1:2] == ["test"] else "kitcher.settings",
    )
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
