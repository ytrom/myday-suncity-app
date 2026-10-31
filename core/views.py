import json
import logging
import re
from datetime import date

from django.conf import settings
from django.db import connections
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from bc.models import ShiftAssignment, TimeEntry
from myday_project.version import VERSION

from . import lanes, services, webpush
from .models import PushSubscription
from .middleware import after_unlock_path, unlock_session

log = logging.getLogger(__name__)

ACTIONS = {"on": "Log on", "off": "Log off"}
PENDING_SECONDS = 180
PHONE = re.compile(r"\+?[0-9 ()-]{6,20}")


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
            return redirect(after_unlock_path(request) or "myday")
        else:
            error = "That PIN didn't match. Try again."
            if services.pin_locked_out(request.staff):
                error = f"Too many tries. Wait {settings.PIN_LOCKOUT_MINUTES} minutes and try again."
    return render(
        request,
        "core/unlock.html",
        {"error": error, **services.pin_lengths()},
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
    ctx = _shift_ctx(request)
    if request.GET.get("finish") and ctx["is_on"]:  # the Finish shift pill: open the Log off PIN pad
        ctx.update(finishing=True, **services.pin_lengths())
    return _page(request, "core/myday.html", "myday", **ctx)


@require_GET
def tasks(request):
    return _page(request, "core/tasks.html", "tasks", tasks=services.tasks_for(request.staff, request.session))


@require_GET
def work(request):
    """Desktop-friendly Work page: today's schedule beside my tickable task list. Only for MYDAY_WORK_EMAILS."""
    if not services.has_work_page(request.staff):
        raise Http404
    mine, others = services.work_lists(request.staff, request.session)
    return _page(request, "core/work.html", "work", schedule=services.todays_schedule(request.staff),
                 mine=mine, others=others, mine_to_do=sum(1 for t in mine if not t.done))


def _week_offset(request, default=0):
    try:
        return max(-52, min(52, int(request.GET.get("week", default))))
    except ValueError:
        return default


def roster(request):
    """One pay week (Friday to Thursday): my shifts, the hours I logged, and my sign-off for payroll.
    ?week=-1 is last week."""
    offset = _week_offset(request)
    start, end = services.pay_week(offset)
    worked, total = services.hours_worked(request.staff, start, end)
    notice = error = None
    if request.method == "POST":
        action = request.POST.get("hours")
        if action == "confirm_all":
            if services.confirm_entries(request.staff, worked):
                notice = "Thanks. Your hours are confirmed."
        elif action == "query":
            note = request.POST.get("note", "").strip()
            if not note:
                error = "Tell us what's not right, e.g. the day and the right times."
            elif worked:
                services.send_query(request.staff, start, worked, note)
                notice = "Thanks. Payroll will check your hours."
    services.mark_confirmations(request.staff.id, worked)
    query = services.week_query(request.staff.id, start)
    return _page(request, "core/roster.html", "roster", rows=_roster_rows(request, start, end), worked=worked,
                 worked_total=services.fmt_minutes(total), week_start=start, week_end=end,
                 offset=offset, prev_week=offset - 1, next_week=offset + 1,
                 query=query, notice=notice, error=error,
                 to_confirm=sum(1 for w in worked if w["can_confirm"]))


@require_POST
def hours_confirm(request, entry_id):
    """Confirm one shift's hours from My Roster (HTMX: re-renders that row)."""
    entry = TimeEntry.objects.filter(id=entry_id, user_id=request.staff.id).first()
    if not entry:
        raise Http404
    day = timezone.localtime(entry.clock_in).date()
    rows, _ = services.hours_worked(request.staff, day, day)
    services.confirm_entries(request.staff, rows, {entry_id})
    row = next(r for r in services.mark_confirmations(request.staff.id, rows) if r["id"] == entry_id)
    return render(request, "core/partials/hours_item.html", {"w": row})


@require_GET
def payroll(request):
    """Managers: everyone's hours for a pay week and whether they've confirmed them. Defaults to last week."""
    if not services.is_manager(request.staff):
        raise Http404
    offset = _week_offset(request, -1)
    start, end = services.pay_week(offset)
    people = services.payroll_week(start, end)
    return _page(request, "core/payroll.html", "payroll", people=people, week_start=start, week_end=end,
                 offset=offset, prev_week=offset - 1, next_week=offset + 1,
                 confirmed=sum(1 for p in people if p["state"] == "confirmed"),
                 total=services.fmt_minutes(sum(p["minutes"] for p in people)))


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
        elif settings.LEAVE_WRITES_ENABLED:
            services.request_leave(request.staff, request.membership, kind, start, end, form.get("reason"))
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


def details(request):
    """My details: opened from the initials in the header."""
    staff = request.staff
    last = services.latest_details(staff)
    notice = error = None
    if request.method == "POST":
        form = {f: " ".join(request.POST.get(f, "").split()) for f in services.DETAIL_FIELDS}
        phones = [form["phone"], form["emergency_phone"]]
        if any(p and not PHONE.fullmatch(p) for p in phones):
            error = "Check the phone numbers. Use digits, spaces and + only."
        elif form["emergency_phone"] and not form["emergency_name"]:
            error = "Add your emergency contact's name."
        else:
            if services.send_details(staff, form):
                notice = "Thanks. Your details have been sent to the office."
                last = services.latest_details(staff)
            else:
                notice = "Nothing changed."
    else:
        form = {f: getattr(last, f) if last else "" for f in services.DETAIL_FIELDS}
        if not last:
            form["phone"] = staff.phone_number or ""
    return _page(request, "core/details.html", "details", form=form, last=last, notice=notice, error=error)


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
        "locate": services.location_check_on(request.staff),
        "clocked_off_at": None if is_on else services.last_clock_off_today(request.staff),
    }


def _shift_card(request, **extra):
    ctx = _shift_ctx(request)
    ctx.update(extra)
    return render(request, "core/partials/shift_card.html", ctx)


def _clocked(request):
    """After Log on / Log off: reload MyDay, so the Finish shift pill appears or the Log on card comes back."""
    resp = _shift_card(request)
    resp["HX-Redirect"] = reverse("myday")
    return resp


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
            "locate": services.location_check_on(request.staff),
            **services.pin_lengths(),
        },
    )


@require_POST
def clock_submit(request, action):
    if action not in ACTIONS:
        return HttpResponseBadRequest()
    def pin_error(message):
        resp = render(request, "core/partials/pin_sheet.html", {
            "action": action, "label": ACTIONS[action], "locate": services.location_check_on(request.staff),
            **services.pin_lengths(), "error": message,
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

    if not services.location_check_on(request.staff):
        services.clock(request.staff, request.membership, action, on_site=None)
        return _clocked(request)
    on_site = services.is_on_site(request, request.POST.get("lat"), request.POST.get("lng"))
    if on_site:
        services.clock(request.staff, request.membership, action, on_site=True)
        return _clocked(request)

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
    return _clocked(request)


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
    if row is None:  # confirmed from a later pay week
        row = ShiftAssignment.objects.select_related("shift").get(id=assignment_id)
        row.confirmed = True
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


# --- Mechanic: lane trouble calls (shared with Lane Marshal) -------------------

def _mechanic_only(request):
    if not services.is_mechanic(request.staff):
        raise Http404


def _marshal_only(request):
    if not services.is_marshal(request.staff):
        raise Http404


def _calls(request, error=None):
    try:
        calls = lanes.open_calls()
    except Exception:  # Lane Marshal's database unreachable: say so, keep polling
        log.exception("Lane calls database error")
        calls, error = [], error or "Can't reach the lane calls right now. Trying again..."
    return {
        "lanes_list": [(lane, [(c, lanes.elapsed(c.age), lanes.elapsed(c.attended_age)) for c in group])
                       for lane, group in lanes.by_lane(calls)],
        "open_count": len(calls),
        "open_ids": ",".join(str(c.id) for c in calls),
        "error": error,
        "poll": settings.MECHANIC_POLL_SECONDS,
    }


@require_GET
def mechanic(request):
    _mechanic_only(request)
    return _page(request, "core/mechanic.html", "mechanic", push_key=webpush.public_key(), **_calls(request))


@require_POST
def mechanic_alerts(request):
    """Turn call alerts on or off for this phone: {"endpoint", "keys": {"p256dh", "auth"}} from
    the browser's push subscription, or {"endpoint", "off": true}."""
    _mechanic_only(request)
    try:
        data = json.loads(request.body)
        endpoint = data["endpoint"]
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError
        if data.get("off"):
            PushSubscription.objects.filter(endpoint=endpoint, user_id=request.staff.id).delete()
            return JsonResponse({"on": False})
        keys = {k: str(data["keys"][k]) for k in ("p256dh", "auth")}
    except (ValueError, KeyError, TypeError):
        return HttpResponseBadRequest()
    PushSubscription.objects.update_or_create(endpoint=endpoint[:1000], defaults={"user_id": request.staff.id, **keys})
    return JsonResponse({"on": True})


@require_POST
def mechanic_alerts_test(request):
    """Send a test alert to this person's phones."""
    _mechanic_only(request)
    sent = webpush.send_all(PushSubscription.objects.filter(user_id=request.staff.id), webpush.TEST_MESSAGE)
    return JsonResponse({"sent": sent})


@require_GET
def mechanic_calls(request):
    _mechanic_only(request)
    return render(request, "core/partials/mechanic_calls.html", _calls(request))


@require_POST
def mechanic_action(request, call_id, action):
    _mechanic_only(request)
    error = None
    try:
        if action == "attend":
            lanes.attend(call_id)
        elif action in ("breakdown", "unbreakdown"):
            lanes.breakdown(call_id, action == "breakdown")
        else:
            return HttpResponseBadRequest()
    except Exception:
        log.exception("Lane calls database error")
        error = "That didn't save. Try again."
    return render(request, "core/partials/mechanic_calls.html", _calls(request, error))


@require_GET
def mechanic_log(request):
    """Log call: a call the mechanic caught before the marshal. First pick the lane..."""
    _mechanic_only(request)
    return _page(request, "core/mechanic_log.html", "mechanic", lanes_range=range(1, settings.LANE_COUNT + 1))


def mechanic_log_lane(request, lane):
    """...then what was fixed (Lane Marshal's issue list, then that issue's details), and it's
    logged as a call that's already fixed, with a Tech Job like any other fix."""
    _mechanic_only(request)
    if not 1 <= lane <= settings.LANE_COUNT:
        raise Http404
    error = None
    try:
        menu = lanes.fix_menu()
    except Exception:
        log.exception("Lane calls database error")
        menu, error = [], "Can't reach the lane calls right now. Try again."
    if request.method == "POST" and menu:
        issue = next((i for i in menu if str(i["id"]) == request.POST.get("issue", "")), None)
        resolution = lanes.resolution_for(menu, request.POST.get("issue"), request.POST.get("option", ""))
        if not resolution:
            error = "Pick what was fixed."
        else:
            try:
                call_id = lanes.log_fixed(lane, issue["name"], request.staff.first_name, resolution)
            except Exception:
                log.exception("Lane calls database error")
                error = "That didn't save. Try again."
            else:
                if settings.TECH_JOB_WRITES_ENABLED and request.membership:
                    try:
                        call = lanes.Call(call_id, str(lane), issue["name"], "", "", 0, 0)
                        lanes.tech_job(call, resolution, request.staff.id, request.membership.bowl_id,
                                       issue["id"], request.POST.get("option", ""))
                    except Exception:
                        log.exception("Tech Job write failed")
                return redirect(_after_fix())
    picked = next((i for i in menu if str(i["id"]) == request.GET.get("issue", "")), None)
    return _page(request, "core/mechanic_log_lane.html", "mechanic", lane=lane, menu=menu, picked=picked, error=error)


def _after_fix():
    """Back to the open calls, or to the MyDay home screen once all lanes are clear."""
    try:
        return "mechanic" if lanes.open_calls() else "myday"
    except Exception:
        log.exception("Lane calls database error")
        return "mechanic"


def mechanic_fix(request, call_id):
    """Fixed: what was fixed? Lane Marshal's issue list, then that issue's details (?issue=ID),
    or No fault. Saved in resolution like Lane Marshal's own Fixed menu."""
    _mechanic_only(request)
    error = None
    try:
        call, menu = lanes.get_call(call_id), lanes.fix_menu()
    except Exception:
        log.exception("Lane calls database error")
        call, menu, error = None, [], "Can't reach the lane calls right now. Try again."
    if not call and not error:
        return redirect("mechanic")  # already fixed (maybe from the tablet)
    if request.method == "POST" and call:
        if request.POST.get("no_fault"):
            resolution = lanes.NO_FAULT
        else:
            resolution = lanes.resolution_for(menu, request.POST.get("issue"), request.POST.get("option", ""))
        if not resolution:
            error = "Pick what was fixed."
        else:
            try:
                lanes.fix(call_id, request.staff.first_name, resolution)
            except Exception:
                log.exception("Lane calls database error")
                error = "That didn't save. Try again."
            else:
                if settings.TECH_JOB_WRITES_ENABLED and request.membership:
                    try:
                        fixed_issue = None if resolution == lanes.NO_FAULT else request.POST.get("issue")
                        lanes.tech_job(call, resolution, request.staff.id, request.membership.bowl_id,
                                       fixed_issue, request.POST.get("option", ""))
                    except Exception:
                        log.exception("Tech Job write failed")  # the call is fixed; the job is extra
                return redirect(_after_fix())
    picked = next((i for i in menu if str(i["id"]) == request.GET.get("issue", "")), None)
    return _page(request, "core/mechanic_fix.html", "mechanic", call=call, menu=menu, picked=picked, error=error)


def _lane_grid(error=None):
    try:
        calls = lanes.open_calls()
    except Exception:
        log.exception("Lane calls database error")
        calls, error = [], error or "Can't reach the lane calls right now. Trying again..."
    grid = []
    for n in range(1, settings.LANE_COUNT + 1):
        on_lane = sorted((c for c in calls if c.lane.strip() == str(n)), key=lambda c: -c.age)
        oldest = on_lane[0] if on_lane else None
        grid.append({"lane": n, "calls": on_lane, "oldest": oldest,
                     "age": lanes.elapsed(oldest.age) if oldest else "", "more": max(len(on_lane) - 1, 0)})
    return {"grid": grid, "open_count": len(calls), "error": error, "poll": settings.MECHANIC_POLL_SECONDS}


@require_GET
def marshal(request):
    _marshal_only(request)
    return _page(request, "core/marshal.html", "marshal", sent=request.GET.get("sent"), **_lane_grid())


@require_GET
def marshal_grid(request):
    _marshal_only(request)
    return render(request, "core/partials/marshal_grid.html", _lane_grid())


def marshal_lane(request, lane):
    _marshal_only(request)
    if not 1 <= lane <= settings.LANE_COUNT:
        raise Http404
    error = None
    # Faults with a pin screen (e.g. Re-spot) open it as a second step: ?fault=Re-spot
    fault = request.GET.get("fault") or request.POST.get("issue", "")
    pin_step = fault in settings.MARSHAL_PIN_CALLS
    if request.method == "POST":
        issue = request.POST.get("issue", "")
        pins = lanes.pins_note(request.POST.getlist("pins")) if pin_step else ""
        if issue not in settings.MARSHAL_CALLS:
            error = "Pick what's wrong first."
        elif pin_step and not pins:
            error = "Tap the pins first."
        else:
            note = " · ".join(x for x in [pins, request.POST.get("note", "").strip()] if x)
            try:
                lanes.report(lane, issue, "", note)
                return redirect(f"{reverse('marshal')}?sent={lane}")
            except Exception:
                log.exception("Lane calls database error")
                error = "That didn't send. Try again, or use the Lane Marshal tablet."
    try:
        already = [(c, lanes.elapsed(c.age)) for c in lanes.open_calls() if c.lane.strip() == str(lane)]
    except Exception:
        log.exception("Lane calls database error")
        already = []
    return _page(request, "core/marshal_lane.html", "marshal", lane=lane, already=already, error=error,
                 faults=[(f, f in settings.MARSHAL_PIN_CALLS) for f in settings.MARSHAL_CALLS],
                 picked=request.POST.get("issue", ""), pin_fault=fault if pin_step else None,
                 pin_rows=lanes.PIN_ROWS, picked_pins=request.POST.getlist("pins"),
                 note=request.POST.get("note", ""), note_max=lanes.NOTE_MAX)


@require_GET
def tabs(request):
    """Re-render just the tab cards (after a tick, confirm or Got it)."""
    return render(request, "core/partials/tabs.html", {"tab": request.GET.get("tab", "myday")})


def _roster_rows(request, start=None, end=None):
    overlay = request.session.get("test_overlay", {}).get("confirmed", [])
    if start:
        rows = list(services.assignments(request.staff, start, (end - start).days + 1))
    else:
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
        return JsonResponse({"ok": True, "bowlcontrol": "ok", "version": VERSION})
    return JsonResponse({"ok": True, "version": VERSION})


@require_GET
def manifest(request):
    data = {
        "id": "/myday",
        "name": "MyDay · Sun City Tenpin",
        "short_name": "MyDay",
        "start_url": "/myday",
        "scope": "/",
        "display": "standalone",
        "background_color": "#1B3F8F",
        "theme_color": "#1B3F8F",
        "icons": [
            {"src": static("core/img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("core/img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("core/img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


@require_GET
def service_worker(request):
    # Caches nothing: staff data always comes fresh from the server. It makes MyDay installable
    # and shows lane-call alerts (Web Push) even when MyDay is closed or the phone is locked.
    js = """\
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
self.addEventListener('push', e => {
  let m = {};
  try { m = e.data ? e.data.json() : {}; } catch (err) {}
  e.waitUntil(self.registration.showNotification(m.title || 'MyDay', {
    body: m.body || '', tag: m.tag || 'myday', renotify: true, requireInteraction: true,
    vibrate: [500, 200, 500, 200, 500], icon: '%(icon)s', badge: '%(icon)s',
    data: { url: m.url || '/mechanic' },
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/mechanic';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('navigate' in c) return c.focus().then(() => c.navigate(url)); }
    return self.clients.openWindow(url);
  }));
});
""" % {"icon": static("core/img/icon-192.png")}
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp
