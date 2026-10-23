import json
from datetime import date

from django.conf import settings
from django.db import connections
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import services
from .middleware import unlock_session

ACTIONS = {"on": "Log on", "off": "Log off"}
PENDING_SECONDS = 180


def _greeting():
    hour = timezone.localtime().hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def _page(request, template, tab, **ctx):
    ctx.update(tab=tab, greeting=_greeting(), today=timezone.localdate())
    return render(request, template, ctx)


# --- PIN lock ----------------------------------------------------------------

def unlock(request):
    error = None
    if request.method == "POST":
        if services.pin_locked_out(request.staff):
            error = f"Too many tries. Wait {settings.PIN_LOCKOUT_MINUTES} minutes and try again."
        elif services.verify_pin(request.staff, request.POST.get("pin")):
            unlock_session(request)
            return redirect("myday")
        else:
            error = "That PIN didn't match. Try again."
            if services.pin_locked_out(request.staff):
                error = f"Too many tries. Wait {settings.PIN_LOCKOUT_MINUTES} minutes and try again."
    return render(
        request,
        "core/unlock.html",
        {"error": error, "pin_length": int(services.settings_value("MYDAY_PIN_LENGTH", "4"))},
        status=200 if not error else 400,
    )


@require_POST
def lock(request):
    request.session.pop("unlocked_until", None)
    return redirect("unlock")


# --- Tabs --------------------------------------------------------------------

@require_GET
def home(request):
    return redirect("myday")


@require_GET
def myday(request):
    return _page(
        request,
        "core/myday.html",
        "myday",
        tasks=services.tasks_for(request.staff, request.session),
        **_shift_ctx(request),
    )


@require_GET
def roster(request):
    return _page(request, "core/roster.html", "roster", rows=_roster_rows(request))


def rdo(request):
    notice = error = None
    form = {}
    if request.method == "POST":
        form = request.POST
        kind = form.get("type", "")
        try:
            start = date.fromisoformat(form.get("start", ""))
            end = date.fromisoformat(form.get("end", "") or form.get("start", ""))
        except ValueError:
            start = end = None
        if kind not in dict(services.leave_types()):
            error = "Pick a type."
        elif not start or not end or end < start:
            error = "Check the dates. The end can't be before the start."
        elif start < timezone.localdate():
            error = "The start date is in the past."
        elif settings.WRITES_ENABLED:
            services.request_leave(request.staff, kind, start, end, form.get("reason"))
            notice = "Request sent. Your manager will reply here."
            form = {}
        else:
            notice = "Test mode: this request was not sent to Vensoft."
            form = {}
    return _page(
        request,
        "core/rdo.html",
        "rdo",
        types=services.leave_types(),
        requests=services.leave_for(request.staff),
        notice=notice,
        error=error,
        form=form,
        min_date=timezone.localdate().isoformat(),
    )


@require_GET
def messages_tab(request):
    return _page(request, "core/messages.html", "messages", notes=services.messages_for(request.staff, request.session))


# --- Shift card: Log on / Log off ---------------------------------------------

def _shift_ctx(request):
    is_on, since, on_site = services.clock_status(request.staff)
    return {
        "shift": services.todays_shift(request.staff),
        "is_on": is_on,
        "since": since,
        "on_site": on_site,
        "clocked_off_at": None if is_on else services.last_clock_off_today(request.staff),
    }


def _shift_card(request, **extra):
    ctx = _shift_ctx(request)
    ctx.update(extra)
    return render(request, "core/partials/shift_card.html", ctx)


@require_GET
def clock_start(request, action):
    if action not in ACTIONS:
        return HttpResponseBadRequest()
    return render(
        request,
        "core/partials/pin_sheet.html",
        {
            "action": action,
            "label": ACTIONS[action],
            "pin_length": int(services.settings_value("MYDAY_PIN_LENGTH", "4")),
        },
    )


@require_POST
def clock_submit(request, action):
    if action not in ACTIONS:
        return HttpResponseBadRequest()
    def pin_error(message):
        resp = render(request, "core/partials/pin_sheet.html", {
            "action": action, "label": ACTIONS[action],
            "pin_length": int(services.settings_value("MYDAY_PIN_LENGTH", "4")), "error": message,
        })
        resp["HX-Retarget"] = "#shift-actions"
        resp["HX-Reswap"] = "innerHTML"
        return resp

    if services.pin_locked_out(request.staff):
        return pin_error(f"Too many tries. Wait {settings.PIN_LOCKOUT_MINUTES} minutes.")
    if not services.verify_pin(request.staff, request.POST.get("pin")):
        if services.pin_locked_out(request.staff):
            return pin_error(f"Too many tries. Wait {settings.PIN_LOCKOUT_MINUTES} minutes.")
        return pin_error("That PIN didn't match. Try again.")

    on_site = services.is_on_site(request, request.POST.get("lat"), request.POST.get("lng"))
    if on_site:
        services.clock(request.staff, request.membership, action, on_site=True)
        return _shift_card(request)

    request.session["pending_clock"] = {"action": action, "at": timezone.now().timestamp()}
    return render(request, "core/partials/offsite.html", {"action": action, "label": ACTIONS[action]})


@require_POST
def clock_offsite(request, action):
    pending = request.session.pop("pending_clock", None)
    fresh = pending and pending["action"] == action and timezone.now().timestamp() - pending["at"] < PENDING_SECONDS
    if not fresh:
        return _shift_card(request, flash="That took too long. Tap the button and enter your PIN again.")
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        request.session["pending_clock"] = pending
        return render(request, "core/partials/offsite.html", {
            "action": action, "label": ACTIONS[action], "error": "Tell us where you're working.",
        })
    services.clock(request.staff, request.membership, action, on_site=False, reason=reason)
    return _shift_card(request)


@require_GET
def shift_card(request):
    request.session.pop("pending_clock", None)
    return _shift_card(request)


# --- Small actions --------------------------------------------------------------

@require_POST
def task_toggle(request, task_id):
    task = services.toggle_task(request.staff, task_id, request.session)
    if not task:
        return HttpResponse(status=404)
    resp = render(request, "core/partials/task_item.html", {"t": task})
    resp["HX-Trigger"] = "counts-changed"
    return resp


@require_POST
def shift_confirm(request, assignment_id):
    if not services.confirm_shift(request.staff, assignment_id, request.session):
        return HttpResponse(status=404)
    row = next((r for r in _roster_rows(request) if r.id == assignment_id), None)
    resp = render(request, "core/partials/roster_item.html", {"a": row})
    resp["HX-Trigger"] = "counts-changed"
    return resp


@require_POST
def message_ack(request, note_id):
    note = services.ack_message(request.staff, note_id, request.session)
    if not note:
        return HttpResponse(status=404)
    resp = render(request, "core/partials/note_item.html", {"n": note})
    resp["HX-Trigger"] = "counts-changed"
    return resp


@require_GET
def tabs(request):
    """Re-render just the tab cards (after a tick, confirm or Got it)."""
    return render(request, "core/partials/tabs.html", {"tab": request.GET.get("tab", "myday")})


def _roster_rows(request):
    overlay = request.session.get("test_overlay", {}).get("confirmed", [])
    rows = services.upcoming_roster(request.staff)
    for a in rows:
        a.confirmed = a.confirmed or a.id in overlay
    return rows


# --- Plumbing ----------------------------------------------------------------

@require_GET
def healthz(request):
    if request.GET.get("db") == "1":
        try:
            with connections["bowlcontrol"].cursor() as c:
                c.execute("SELECT 1")
        except Exception as exc:
            return JsonResponse({"ok": False, "bowlcontrol": exc.__class__.__name__}, status=503)
        return JsonResponse({"ok": True, "bowlcontrol": "ok"})
    return JsonResponse({"ok": True})


@require_GET
def manifest(request):
    data = {
        "name": "MyDay · Sun City Tenpin",
        "short_name": "MyDay",
        "start_url": "/myday",
        "scope": "/",
        "display": "standalone",
        "background_color": "#1B3F8F",
        "theme_color": "#1B3F8F",
        "icons": [
            {"src": static("core/img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("core/img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


@require_GET
def service_worker(request):
    # Minimal worker so phones offer "Add to Home Screen". It caches nothing: staff data
    # always comes fresh from the server. Push notifications get added here later.
    js = (
        "self.addEventListener('install', () => self.skipWaiting());\n"
        "self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));\n"
        "self.addEventListener('fetch', () => {});\n"
    )
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp
