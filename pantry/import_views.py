from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from pantry.import_forms import CandidateFormSet, PhotoImportForm, TextImportForm
from pantry.models import ImportDraft, WorkerStatus
from pantry.services import imports
from pantry.services.quantities import Conflict, DomainError

SALT = "kitcher.import.edit.v1"


def revision_token(draft):
    return signing.dumps(
        {"owner": draft.owner_id, "draft": str(draft.pk), "revision": draft.revision},
        salt=SALT,
    )


def read_revision(owner, draft, token):
    try:
        value = signing.loads(token, salt=SALT, max_age=86400)
    except signing.BadSignature as exc:
        raise DomainError("Draft edit expired. Reload before continuing.") from exc
    if value.get("owner") != owner.pk or value.get("draft") != str(draft.pk):
        raise DomainError("Invalid draft edit.")
    return value.get("revision")


@login_required
def import_list(request):
    text_form, photo_form = TextImportForm(), PhotoImportForm()
    if request.method == "POST":
        try:
            if request.POST.get("action") == "text":
                text_form = TextImportForm(request.POST)
                if text_form.is_valid():
                    draft = imports.create_text(
                        request.user, text_form.cleaned_data["text"]
                    )
                    return redirect("import_review", pk=draft.pk)
            elif request.POST.get("action") == "photo":
                photo_form = PhotoImportForm(request.POST, request.FILES)
                if len(request.FILES.getlist("photo")) != 1 or len(request.FILES) != 1:
                    photo_form.add_error(None, "Upload exactly one photo.")
                elif photo_form.is_valid():
                    draft = imports.create_photo(
                        request.user,
                        **{
                            "upload": photo_form.cleaned_data["photo"],
                            "source_type": photo_form.cleaned_data["source_type"],
                        },
                    )
                    return redirect("import_review", pk=draft.pk)
        except DomainError as exc:
            (
                text_form if request.POST.get("action") == "text" else photo_form
            ).add_error(None, str(exc))
    worker = WorkerStatus.objects.filter(name="imports").first()
    return render(
        request,
        "pantry/imports.html",
        {
            "text_form": text_form,
            "photo_form": photo_form,
            "drafts": ImportDraft.objects.filter(owner=request.user)[:40],
            "worker": worker,
            "cleanup_status": WorkerStatus.objects.filter(name="cleanup").first(),
            "worker_recent": bool(
                worker and (timezone.now() - worker.last_seen).total_seconds() < 180
            ),
        },
    )


def render_review(request, draft, *, forms=None, error="", status=200):
    if forms is None:
        forms = CandidateFormSet(
            initial=draft.candidates,
            form_kwargs={"owner": request.user, "source_type": draft.source_type},
        )
    job = getattr(draft, "job", None)
    return render(
        request,
        "pantry/import_review.html",
        {
            "draft": draft,
            "formset": forms,
            "revision_token": revision_token(draft),
            "job": job,
            "source_available": bool(job and imports.source_available(job)),
            "can_retry": bool(
                job
                and job.state in {"failed", "cancelled"}
                and imports.source_available(job)
            ),
            "error": error,
        },
        status=status,
    )


@login_required
def import_review(request, pk):
    draft = get_object_or_404(ImportDraft, pk=pk, owner=request.user)
    if request.method == "POST":
        forms = CandidateFormSet(
            request.POST,
            initial=draft.candidates,
            form_kwargs={"owner": request.user, "source_type": draft.source_type},
        )
        revision = draft.revision
        try:
            revision = read_revision(
                request.user, draft, request.POST.get("revision_token", "")
            )
            if forms.is_valid():
                rows = [
                    {
                        **row,
                        "ingredient": row["ingredient"].pk
                        if row.get("ingredient")
                        else None,
                    }
                    for row in forms.cleaned_data
                    if row and not row.get("remove")
                ]
                # Removed forms still need to be excluded, while empty extra forms are ignored.
                draft = imports.edit(
                    request.user,
                    draft.pk,
                    revision,
                    rows,
                    duplicate_ack=request.POST.get("duplicate_ack") == "on",
                )
                if request.POST.get("action") == "preview":
                    reviewed = imports.preview(request.user, draft.pk)
                    return render(request, "pantry/preview.html", {"preview": reviewed})
                messages.success(request, "Review saved. Inventory has not changed.")
                return redirect("import_review", pk=draft.pk)
        except Conflict as exc:
            draft.refresh_from_db()
            return render_review(request, draft, error=str(exc), status=409)
        except DomainError as exc:
            # A successful edit followed by a stock reconciliation error uses the new revision.
            return render_review(
                request,
                draft,
                forms=forms if draft.revision == revision else None,
                error=str(exc),
                status=422,
            )
        return render_review(request, draft, forms=forms, status=422)
    return render_review(request, draft)


@login_required
@require_POST
def import_action(request, pk, action):
    draft = get_object_or_404(ImportDraft, pk=pk, owner=request.user)
    try:
        revision = read_revision(
            request.user, draft, request.POST.get("revision_token", "")
        )
        if action == "cancel":
            imports.cancel(request.user, draft.pk, revision)
        elif action == "retry":
            imports.retry(request.user, draft.pk, revision)
        else:
            raise DomainError("Unknown import action.")
    except DomainError as exc:
        draft.refresh_from_db()
        return render_review(request, draft, error=str(exc), status=409)
    return redirect("import_review", pk=draft.pk)


@login_required
def import_source(request, pk):
    draft = get_object_or_404(ImportDraft, pk=pk, owner=request.user)
    job = getattr(draft, "job", None)
    if (
        job is None
        or draft.status in {"applied", "cancelled"}
        or not imports.source_available(job)
    ):
        from django.http import Http404

        raise Http404("The private source is unavailable or expired.")
    response = FileResponse(
        imports.source_path(job.source_name).open("rb"), content_type="image/jpeg"
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
