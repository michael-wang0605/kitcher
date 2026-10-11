from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from pantry.models import WorkerStatus
from pantry.services.backups import read_status


@login_required
def operations(request):
    worker = WorkerStatus.objects.filter(name="imports").first()
    cleanup = WorkerStatus.objects.filter(name="cleanup").first()
    online = bool(
        worker
        and not worker.detail.startswith("Import worker stopped")
        and (timezone.now() - worker.last_seen).total_seconds() < 180
    )
    backup = read_status()
    return render(
        request,
        "pantry/operations.html",
        {
            "worker": worker,
            "worker_online": online,
            "cleanup": cleanup,
            "backup": backup,
            "phone_url": getattr(settings, "PHONE_URL", ""),
        },
    )
