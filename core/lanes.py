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


def fix(call_id, who):
    with _cursor() as c:
        c.execute("UPDATE tech_control_troublecall SET fixed_at = NOW(), fixed_by = %s "
                  "WHERE id = %s AND fixed_at IS NULL", [(who or "MECH")[:FIXED_BY_MAX], call_id])


def report(lane, issue):
    """A new call from a lane marshal, as Lane Marshal's own screen would log it."""
    with _cursor() as c:
        c.execute('INSERT INTO tech_control_troublecall ("ISSUE", "LANE", "OPTION", "NOTE", "DATE", "ID") '
                  "VALUES (%s, %s, '', '', NOW(), 'APP')", [issue, str(lane)])


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
