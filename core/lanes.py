"""Lane trouble calls, shared with Lane Marshal (tech_control_troublecall in Suncity_Database).

The lane kiosks log a call; the Mechanic screen shows the open ones and marks them
"on my way" (attended_at) and "fixed" (fixed_at, fixed_by), exactly as Lane Marshal's
/mechanic page does, so both screens stay in step.

Times are worked out inside Postgres with the server's default time zone, the same one
Lane Marshal's connection uses, so NOW() and the stored times always agree.
"""
from dataclasses import dataclass

from django.db import connections

WINDOW = "12 hours"  # same as Lane Marshal: older unfixed calls drop off the screen
URGENT_SECONDS = 120
FIXED_BY_MAX = 10


@dataclass
class Call:
    id: int
    lane: str
    issue: str
    option: str
    note: str
    age: int  # seconds since the call came in
    attended_age: int | None  # seconds since someone said "on my way"

    @property
    def urgent(self):
        return self.age > URGENT_SECONDS and self.attended_age is None

    @property
    def lane_sort(self):
        lane = str(self.lane or "").strip()
        return (0, int(lane), "") if lane.isdigit() else (1, 0, lane)


def _cursor():
    c = connections["lanes"].cursor()
    c.execute("SET TIME ZONE DEFAULT")
    return c


def open_calls():
    with _cursor() as c:
        c.execute(f"""
            SELECT id, "LANE", "ISSUE", "OPTION", "NOTE",
                   EXTRACT(EPOCH FROM NOW() - "DATE")::int,
                   CASE WHEN attended_at IS NULL THEN NULL
                        ELSE EXTRACT(EPOCH FROM NOW() - attended_at)::int END
            FROM tech_control_troublecall
            WHERE fixed_at IS NULL AND "DATE" > NOW() - INTERVAL '{WINDOW}'
            ORDER BY "DATE"
        """)
        calls = [Call(r[0], str(r[1] or ""), r[2] or "", r[3] or "", r[4] or "", max(r[5] or 0, 0), r[6])
                 for r in c.fetchall()]
    return sorted(calls, key=lambda x: x.lane_sort)


def attend(call_id):
    with _cursor() as c:
        c.execute("UPDATE tech_control_troublecall SET attended_at = NOW() "
                  "WHERE id = %s AND attended_at IS NULL AND fixed_at IS NULL", [call_id])


NO_FAULT = "no_fault"


def fix(call_id, who, resolution=""):
    """Mark a call fixed with what was done, saved in resolution the way Lane Marshal's own
    Fixed menu does: "Issue — Detail", "Issue", or "no_fault"."""
    with _cursor() as c:
        c.execute("UPDATE tech_control_troublecall SET fixed_at = NOW(), fixed_by = %s, resolution = %s "
                  "WHERE id = %s AND fixed_at IS NULL", [(who or "MECH")[:FIXED_BY_MAX], resolution, call_id])


def fix_menu():
    """What-was-fixed choices: Lane Marshal's issue list with each issue's details, as
    [{"id", "name", "options": [names]}] in the same order as its Fixed menu."""
    with _cursor() as c:
        c.execute('SELECT "Issue_ID", "Issue" FROM issue WHERE "Active" = true ORDER BY "Issue_ID"')
        issues = [{"id": r[0], "name": (r[1] or "").strip(), "options": []} for r in c.fetchall()]
        c.execute('SELECT detail_issue, "Detail" FROM issue_options WHERE "Active" = true ORDER BY id')
        by_id = {i["id"]: i for i in issues}
        for issue_id, detail in c.fetchall():
            if issue_id in by_id and (detail or "").strip():
                by_id[issue_id]["options"].append(detail.strip())
    return issues


def resolution_for(menu, issue_id, option=""):
    """The resolution text for a choice, or None if it isn't on the menu."""
    issue = next((i for i in menu if str(i["id"]) == str(issue_id)), None)
    if not issue:
        return None
    if option:
        return f"{issue['name']} — {option}" if option in issue["options"] else None
    return None if issue["options"] else issue["name"]


def _plain(name):
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def tech_job(call, resolution, user_id, bowl_id):
    """Log the fix as a finished Tech Job in Vensoft (bowlcontrol.tech_techentry), the same way
    Lane Marshal's Fixed does: title is the marshal's call, description is
    "call · note · what was fixed", and the fault is the Vensoft issue option matching the call."""
    fixed = "No fault found" if resolution == NO_FAULT else resolution
    description = " · ".join(x for x in [call.issue, call.note, fixed] if x)
    lane = int(call.lane) if str(call.lane).isdigit() else 0
    with connections["bowlcontrol"].cursor() as c:
        c.execute("SELECT id, name FROM tech_issueoption WHERE bowl_id = %s", [bowl_id])
        option_id = next((r[0] for r in c.fetchall() if _plain(r[1]) == _plain(call.issue)), None)
        c.execute(
            "INSERT INTO tech_techentry (type, title, description, lane_number, submitted_via, created_at, "
            "updated_at, dealt_with, dealt_via, dealt_at, bowl_id, created_by_id, dealt_by_id, updated_by_id, "
            "issue_option_id) VALUES ('ISSUE', %s, %s, %s, 'LANE_MR', NOW(), NOW(), true, 'LANE_MR', NOW(), "
            "%s, %s, %s, %s, %s)",
            [call.issue[:200], description, lane, bowl_id, user_id, user_id, user_id, option_id])


def get_call(call_id):
    return next((c for c in open_calls() if c.id == call_id), None)


NOTE_MAX = 200


def report(lane, issue, option="", note=""):
    """A new call from a lane marshal, as Lane Marshal's own screen would log it."""
    note = " ".join((note or "").split())[:NOTE_MAX]
    with _cursor() as c:
        c.execute('INSERT INTO tech_control_troublecall ("ISSUE", "LANE", "OPTION", "NOTE", "DATE", "ID") '
                  "VALUES (%s, %s, %s, %s, NOW(), 'APP')", [issue, str(lane), option, note])


PIN_ROWS = [[7, 8, 9, 10], [4, 5, 6], [2, 3], [1]]  # the pin deck as the marshal sees it


def pins_note(values):
    """The pins picked on the pin screen, as Lane Marshal stores them in NOTE, e.g. "Pins: 3, 5"."""
    pins = sorted({int(v) for v in values if str(v).isdigit() and 1 <= int(v) <= 10})
    return ("Pins: " + ", ".join(map(str, pins))) if pins else ""


def by_lane(calls):
    """[(lane, [calls])] in lane order."""
    groups = {}
    for call in calls:
        groups.setdefault(call.lane, []).append(call)
    return list(groups.items())


def elapsed(seconds):
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"
