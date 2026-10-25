"""Queries and rules for MyDay. Views stay thin; everything that touches BowlControl is here."""
import hashlib
import ipaddress
import math
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from bc.models import (
    BowlNote,
    BowlNoteAck,
    LeaveRequest,
    Membership,
    ShiftAssignment,
    TechTask,
    TechTaskAssignee,
    TimeEntry,
    User,
)

from .models import DetailsUpdate, HoursConfirmation, PinAttempt, SiteCheck, StaffSetting
from .pins import pin_matches


def today():
    return timezone.localdate()


# --- Who is this -------------------------------------------------------------

def staff_for_email(email):
    if not email:
        return None
    return User.objects.filter(email__iexact=email.strip(), is_active=True).first()


def membership_for(user):
    qs = Membership.objects.filter(user_id=user.id).exclude(pin_hash__isnull=True).exclude(pin_hash="")
    if settings.DEFAULT_BOWL_ID:
        preferred = qs.filter(bowl_id=int(settings.DEFAULT_BOWL_ID)).first()
        if preferred:
            return preferred
    return qs.order_by("id").first()


# --- PIN ---------------------------------------------------------------------

def pin_locked_out(user):
    since = timezone.now() - timedelta(minutes=settings.PIN_LOCKOUT_MINUTES)
    return PinAttempt.objects.filter(user_id=user.id, at__gte=since).count() >= settings.PIN_MAX_ATTEMPTS


def pin_lengths():
    """Shortest and longest PIN the keypad accepts (the login clock allows 4 to 6 digits)."""
    low = int(settings_value("MYDAY_PIN_MIN", "4"))
    return {"pin_min": low, "pin_max": max(low, int(settings_value("MYDAY_PIN_MAX", "6")))}


def verify_pin(user, pin):
    """True if the PIN matches the login-clock PIN (Django-hashed in accounts_membership)."""
    if pin_locked_out(user):
        return False
    pin = (pin or "").strip()
    ok = bool(pin) and pin.isdigit() and any(
        pin_matches(pin, m.pin_hash)
        for m in Membership.objects.filter(user_id=user.id).exclude(pin_hash__isnull=True).exclude(pin_hash="")
    )
    if ok:
        PinAttempt.objects.filter(user_id=user.id).delete()
    else:
        PinAttempt.objects.create(user_id=user.id)
    return ok


# --- On site / off site ------------------------------------------------------

def _distance_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def client_ip(request):
    return request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("REMOTE_ADDR", "")


def on_venue_network(request):
    ip = client_ip(request)
    for allowed in settings.VENUE_PUBLIC_IPS:
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_network(allowed, strict=False):
                return True
        except ValueError:
            continue
    return False


def location_check_on(user):
    """Whether Log on / Log off asks for this person's location (off unless switched on)."""
    setting = StaffSetting.objects.filter(user_id=user.id).first()
    return setting.location_check if setting else settings.LOCATION_CHECK_DEFAULT


def set_location_check(user, on):
    StaffSetting.objects.update_or_create(user_id=user.id, defaults={"location_check": on})


LANE_TOOLS = ("mechanic", "marshal")


def is_tech(user):
    """A tech in Vensoft: any of their memberships has one of MYDAY_TECH_ROLES."""
    roles = {(r or "").strip().upper() for r in Membership.objects.filter(user_id=user.id).values_list("role", flat=True)}
    return bool(roles & set(settings.TECH_ROLES))


def lane_tools(user):
    """Which lane-call screens this person gets: {"mechanic": bool, "marshal": bool}.
    Techs (by their Vensoft role) get both; anyone else only if switched on for them
    (manage.py mechanic / marshal). Nothing while the lane-calls feature is off."""
    off = dict.fromkeys(LANE_TOOLS, False)
    if not settings.MECHANIC_ENABLED or not user:
        return off
    if is_tech(user):
        return dict.fromkeys(LANE_TOOLS, True)
    s = StaffSetting.objects.filter(user_id=user.id).first()
    return {t: getattr(s, t) for t in LANE_TOOLS} if s else off


def is_mechanic(user):
    return lane_tools(user)["mechanic"]


def is_marshal(user):
    return lane_tools(user)["marshal"]


def set_lane_tool(user, tool, on):
    StaffSetting.objects.update_or_create(user_id=user.id, defaults={tool: on})


def set_mechanic(user, on):
    set_lane_tool(user, "mechanic", on)


def is_on_site(request, lat, lng):
    """Venue wifi, or a phone location within the radius. Coordinates are used here and dropped."""
    if on_venue_network(request):
        return True
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False  # location blocked or unavailable counts as off site
    return _distance_m(lat, lng, settings.VENUE_LAT, settings.VENUE_LNG) <= settings.VENUE_RADIUS_M


# --- Clock on / off ----------------------------------------------------------

def open_entry(user):
    return TimeEntry.objects.filter(user_id=user.id, clock_out__isnull=True).order_by("-clock_in").first()


def clock_status(user):
    """Returns (is_on, since, on_site) from BowlControl, or from MyDay's own record in test mode."""
    entry = open_entry(user)
    last = SiteCheck.objects.filter(user_id=user.id, at__date=today()).first()
    if entry:
        on_site = last.on_site if (last and last.action == "on") else True
        return True, entry.clock_in, on_site
    if not settings.CLOCK_WRITES_ENABLED and last and last.action == "on":
        return True, last.at, last.on_site
    return False, None, True


def last_clock_off_today(user):
    if settings.CLOCK_WRITES_ENABLED:
        entry = (
            TimeEntry.objects.filter(user_id=user.id, clock_out__date=today())
            .order_by("-clock_out")
            .first()
        )
        return entry.clock_out if entry else None
    last = SiteCheck.objects.filter(user_id=user.id, at__date=today()).first()
    return last.at if last and last.action == "off" else None


def payable_minutes(clock_in, clock_out):
    """As the login clock works it out: time on the clock rounded to the nearest 15 minutes."""
    quarters = (clock_out - clock_in).total_seconds() / 900
    return int(math.floor(quarters + 0.5)) * 15


def clock(user, membership, action, on_site, reason=""):
    """Log on or off. Writes core_timeentry only when clock writes are switched on."""
    reason = (reason or "").strip()[:200]
    entry_id = None
    if settings.CLOCK_WRITES_ENABLED:
        note = f"Off site: {reason}" if on_site is False else ""
        now = timezone.now()
        with transaction.atomic(using="bowlcontrol"):
            current = open_entry(user)
            if action == "on" and not current:
                entry = TimeEntry.objects.create(
                    user_id=user.id,
                    bowl_id=membership.bowl_id,
                    clock_in=now,
                    source=settings_value("MYDAY_CLOCK_SOURCE", "kiosk"),
                    note="; ".join(x for x in ["MyDay", note] if x),
                    payable_source="AUTO",
                    created_at=now,
                    is_locked=False,
                )
                entry_id = entry.id
            elif action == "off" and current and not current.is_locked:
                current.clock_out = now
                current.note = "; ".join(x for x in [current.note, note] if x)
                if current.payable_source == "AUTO":
                    current.payable_minutes = payable_minutes(current.clock_in, now)
                    current.payable_updated_at = now
                current.save(update_fields=["clock_out", "note", "payable_minutes", "payable_updated_at"])
                entry_id = current.id
    SiteCheck.objects.create(
        user_id=user.id, action=action, on_site=on_site, reason=reason if on_site is False else "", time_entry_id=entry_id
    )


# --- Today's shift and roster ------------------------------------------------

def assignments(user, start, days):
    return (
        ShiftAssignment.objects.select_related("shift")
        .filter(user_id=user.id, shift__date__gte=start, shift__date__lt=start + timedelta(days=days))
        .order_by("shift__date", "shift__start_time")
    )


def todays_shift(user):
    a = assignments(user, today(), 1).first()
    return a.shift if a else None


def upcoming_roster(user, days=14):
    return list(assignments(user, today(), days))


PAY_WEEK_START = 4  # Friday: Sun City's pay week runs Friday to Thursday


def pay_week(offset=0):
    """(first, last) day of the pay week `offset` weeks from the current one."""
    first = today() - timedelta(days=(today().weekday() - PAY_WEEK_START) % 7) + timedelta(weeks=offset)
    return first, first + timedelta(days=6)


def hours_worked(user, start, end):
    """My Log on / Log off entries from the login clock (and MyDay) between two dates, oldest first,
    each marked whether I was rostered that day. Returns (rows, total_minutes)."""
    entries = TimeEntry.objects.filter(
        user_id=user.id, clock_in__date__gte=start, clock_in__date__lte=end
    ).order_by("clock_in")
    rostered = set(
        ShiftAssignment.objects.filter(user_id=user.id, shift__date__gte=start, shift__date__lte=end)
        .values_list("shift__date", flat=True)
    )
    now = timezone.now()
    rows, total = [], 0
    for e in entries:
        day = timezone.localtime(e.clock_in).date()
        if e.clock_out:
            minutes = e.payable_minutes if e.payable_minutes is not None else payable_minutes(e.clock_in, e.clock_out)
        else:
            minutes = int((now - e.clock_in).total_seconds() // 60)
        total += minutes
        rows.append({"id": e.id, "day": day, "clock_in": e.clock_in, "clock_out": e.clock_out, "minutes": minutes,
                     "hours": f"{minutes // 60}h {minutes % 60:02d}m", "rostered": day in rostered})
    return rows, total


def fmt_minutes(total):
    return f"{total // 60}h {total % 60:02d}m"


def hours_fingerprint(rows):
    raw = "|".join(f"{r['id']}:{r['clock_in'].isoformat()}:{r['clock_out'] and r['clock_out'].isoformat()}:{r['minutes']}"
                   for r in rows)
    return hashlib.sha256(raw.encode()).hexdigest()


def hours_status(user_id, week_start, rows):
    """Where this person's sign-off stands for the week: (state, confirmation).
    state is none / confirmed / query / changed (confirmed, but the hours have changed since)."""
    c = HoursConfirmation.objects.filter(user_id=user_id, week_start=week_start).first()
    if not c:
        return "none", None
    if c.fingerprint != hours_fingerprint(rows):
        return "changed", c
    return c.status, c


def confirm_hours(user, week_start, rows, status, note=""):
    total = sum(r["minutes"] for r in rows)
    HoursConfirmation.objects.update_or_create(
        user_id=user.id, week_start=week_start,
        defaults={"status": status, "note": " ".join((note or "").split())[:300],
                  "total_minutes": total, "fingerprint": hours_fingerprint(rows)},
    )


def is_manager(user):
    if not user:
        return False
    roles = {(r or "").strip().upper() for r in Membership.objects.filter(user_id=user.id).values_list("role", flat=True)}
    return bool(roles & set(settings.MANAGER_ROLES))


def payroll_week(start, end):
    """Everyone with hours or a shift in the pay week: name, hours, sign-off state. Sorted by name."""
    users = {}
    for e in TimeEntry.objects.filter(clock_in__date__gte=start, clock_in__date__lte=end).values_list("user_id", flat=True):
        users[e] = True
    for u in ShiftAssignment.objects.filter(shift__date__gte=start, shift__date__lte=end).values_list("user_id", flat=True):
        users[u] = True
    people = []
    for user in User.objects.filter(id__in=users).order_by("first_name", "last_name"):
        rows, total = hours_worked(user, start, end)
        state, c = hours_status(user.id, start, rows)
        people.append({"user": user, "total": fmt_minutes(total), "minutes": total, "state": state,
                       "note": c.note if c else "", "extra": sum(1 for r in rows if not r["rostered"]),
                       "open": any(not r["clock_out"] for r in rows)})
    return people


def confirm_shift(user, assignment_id, session):
    a = ShiftAssignment.objects.filter(id=assignment_id, user_id=user.id).first()
    if not a:
        return None
    if settings.WRITES_ENABLED:
        ShiftAssignment.objects.filter(id=a.id).update(confirmed=True)
        a.confirmed = True
    else:
        _test_overlay(session, "confirmed").add(a.id)
        session.modified = True
        a.confirmed = True
    return a


# --- Tasks -------------------------------------------------------------------

def tasks_for(user, session):
    """Today's tasks assigned to me, plus today's unassigned ones, plus my open ones from the last 7 days."""
    start = today()
    mine = TechTaskAssignee.objects.filter(techtask_id=OuterRef("pk"), user_id=user.id)
    anyone = TechTaskAssignee.objects.filter(techtask_id=OuterRef("pk"))
    qs = (
        TechTask.objects.annotate(is_mine=Exists(mine), has_assignee=Exists(anyone))
        .filter(
            Q(created_at__date=start, is_mine=True)
            | Q(created_at__date=start, has_assignee=False)
            | Q(created_at__date__gte=start - timedelta(days=7), is_mine=True, status="OPEN")
        )
        .order_by("created_at")
    )
    done_overlay = _test_overlay(session, "done")
    undone_overlay = _test_overlay(session, "undone")
    tasks = list(qs[:50])
    for t in tasks:
        t.done = (t.status == "DONE" or t.id in done_overlay) and t.id not in undone_overlay
    return tasks


def toggle_task(user, task_id, session):
    task = next((t for t in tasks_for(user, session) if t.id == task_id), None)
    if not task:
        return None
    if settings.WRITES_ENABLED:
        if task.done:
            TechTask.objects.filter(id=task.id).update(status="OPEN", completed_at=None, completed_by=None)
        else:
            TechTask.objects.filter(id=task.id).update(
                status="DONE", completed_at=timezone.now(), completed_by_id=user.id
            )
    else:
        done, undone = _test_overlay(session, "done"), _test_overlay(session, "undone")
        if task.done:
            done.discard(task.id)
            undone.add(task.id)
        else:
            undone.discard(task.id)
            done.add(task.id)
        session.modified = True
    task.done = not task.done
    return task


# --- Staff messages ----------------------------------------------------------

def messages_for(user, session):
    now = timezone.now()
    acked = BowlNoteAck.objects.filter(note_id=OuterRef("pk"), user_id=user.id)
    notes = list(
        BowlNote.objects.filter(Q(target_all_staff=True) | Q(assigned_user_id=user.id))
        .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .annotate(is_acked=Exists(acked))
        .order_by("-publish_at", "-id")[:30]
    )
    overlay = _test_overlay(session, "acked")
    for n in notes:
        n.unread = not (n.is_acked or n.id in overlay)
    return notes


def ack_message(user, note_id, session):
    note = next((n for n in messages_for(user, session) if n.id == note_id), None)
    if not note:
        return None
    if note.unread:
        if settings.WRITES_ENABLED:
            BowlNoteAck.objects.get_or_create(
                note_id=note.id, user_id=user.id, defaults={"acknowledged_at": timezone.now()}
            )
        else:
            _test_overlay(session, "acked").add(note.id)
            session.modified = True
    note.unread = False
    return note


# --- RDO and leave -----------------------------------------------------------

def leave_types():
    # The types Vensoft uses (an RDO is unpaid leave there); override with MYDAY_LEAVE_TYPES.
    raw = settings_value("MYDAY_LEAVE_TYPES", "UNPAID:RDO / day off,ANNUAL:Annual leave")
    return [tuple(x.split(":", 1)) for x in raw.split(",") if ":" in x]


def leave_for(user):
    names = dict(leave_types())
    rows = list(LeaveRequest.objects.filter(user_id=user.id).order_by("-start")[:10])
    for r in rows:
        r.label = names.get(r.type, (r.type or "").title())
    return rows


def request_leave(user, membership, kind, start, end, reason):
    if not settings.LEAVE_WRITES_ENABLED:
        return None
    return LeaveRequest.objects.create(
        user_id=user.id,
        bowl_id=membership.bowl_id,
        type=kind,
        start=start,
        end=end,
        status=settings_value("MYDAY_LEAVE_NEW_STATUS", "PENDING"),
        reason=(reason or "").strip()[:1000],
        created_at=timezone.now(),
    )


# --- helpers -----------------------------------------------------------------

def settings_value(name, default):
    import os

    return os.environ.get(name, default)


def _test_overlay(session, key):
    """In test mode, ticks/acks/confirms live only in the session so the shell can be clicked through."""
    store = session.setdefault("test_overlay", {})
    ids = store.setdefault(key, [])

    class _Set:
        def __contains__(self, x):
            return x in ids

        def add(self, x):
            if x not in ids:
                ids.append(x)

        def discard(self, x):
            if x in ids:
                ids.remove(x)

    return _Set()


# --- My details ---------------------------------------------------------------

DETAIL_FIELDS = ["phone", "address", "emergency_name", "emergency_phone", "emergency_relationship"]


def latest_details(user):
    """The last details this person sent, whether or not the office has copied them yet."""
    return DetailsUpdate.objects.filter(user_id=user.id).first()


def send_details(user, data):
    """Save a details update for the office. Returns it, or None if nothing changed."""
    last = latest_details(user)
    current = {f: getattr(last, f) if last else "" for f in DETAIL_FIELDS}
    if not last:
        current["phone"] = user.phone_number or ""
    if all(data[f] == current[f] for f in DETAIL_FIELDS):
        return None
    return DetailsUpdate.objects.create(user_id=user.id, **data)
