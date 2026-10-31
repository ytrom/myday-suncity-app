"""Watch for new lane calls and push an alert to every tech's phone that has call alerts on.

Runs on its own in the myday-alerts container (docker-compose.yml):

    python manage.py call_alerts           # keep watching
    python manage.py call_alerts --test EMAIL   # send that person a test alert and stop
"""
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from bc.models import User
from core import lanes, services, webpush
from core.models import PushSubscription

log = logging.getLogger(__name__)


def tech_phones():
    """Phones with alerts on, for people who still have the Mechanic screen."""
    subs = list(PushSubscription.objects.all())
    users = {u.id: u for u in User.objects.filter(id__in={s.user_id for s in subs})}
    return [s for s in subs if services.is_mechanic(users.get(s.user_id))]


def new_calls(calls, seen):
    """Calls that weren't open last time we looked (None: first look, nothing is new)."""
    return [] if seen is None else [c for c in calls if c.id not in seen]


class Command(BaseCommand):
    help = "Push lane-call alerts to the techs' phones."

    def add_arguments(self, parser):
        parser.add_argument("--test", metavar="EMAIL")

    def handle(self, test=None, **opts):
        if test:
            user = User.objects.filter(email__iexact=test.strip()).first()
            if not user:
                raise CommandError("No Vensoft user with that email.")
            subs = PushSubscription.objects.filter(user_id=user.id)
            sent = webpush.send_all(subs, webpush.TEST_MESSAGE)
            self.stdout.write(f"{user.full_name}: sent to {sent} of {len(subs)} phone(s)")
            return
        if not settings.MECHANIC_ENABLED:
            self.stdout.write("MYDAY_MECHANIC is off: no call alerts.")
        webpush.vapid_key()
        log.info("Call alerts: watching for new lane calls every %ss", settings.CALL_ALERTS_POLL_SECONDS)
        seen = None
        while True:
            seen = self.check(seen)
            time.sleep(settings.CALL_ALERTS_POLL_SECONDS)

    def check(self, seen):
        """One look at the open calls. Returns the ids to remember for next time."""
        close_old_connections()
        if not settings.MECHANIC_ENABLED:
            return seen
        try:
            calls = lanes.open_calls()
        except Exception:
            log.exception("Lane calls database error")
            return seen
        fresh = new_calls(calls, seen)
        if fresh:
            try:
                phones = tech_phones()
                for call in fresh:
                    sent = webpush.send_all(phones, webpush.call_message(call))
                    log.info("Call %s on lane %s: alert sent to %s phone(s)", call.id, call.lane, sent)
            except Exception:
                log.exception("Call alert failed")
        return {c.id for c in calls}
