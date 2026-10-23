from functools import cached_property

from django.conf import settings
from django.utils import timezone

from . import services


class TabCounts:
    """Counts for the tab cards, worked out only if a template asks for them."""

    def __init__(self, request):
        self.request = request
        self.user = request.staff

    @cached_property
    def tasks_to_do(self):
        return sum(1 for t in services.tasks_for(self.user, self.request.session) if not t.done)

    @cached_property
    def shifts_to_confirm(self):
        overlay = self.request.session.get("test_overlay", {}).get("confirmed", [])
        return sum(1 for a in services.upcoming_roster(self.user) if not a.confirmed and a.id not in overlay)

    @cached_property
    def leave_pending(self):
        return sum(1 for r in services.leave_for(self.user) if (r.status or "").lower() == "pending")

    @cached_property
    def unread_messages(self):
        return sum(1 for n in services.messages_for(self.user, self.request.session) if n.unread)


def myday(request):
    staff = getattr(request, "staff", None)
    return {
        "staff": staff,
        "today": timezone.localdate(),
        "counts": TabCounts(request) if staff else None,
        "writes_enabled": settings.WRITES_ENABLED,
        "leave_writes_enabled": settings.LEAVE_WRITES_ENABLED,
        "identity_test_mode": getattr(request, "identity_test_mode", False),
    }
