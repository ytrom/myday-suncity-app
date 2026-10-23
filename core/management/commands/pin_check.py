"""Work out why a PIN isn't accepted, without printing the PIN or any hash.

    python manage.py pin_check someone@example.com     # asks for the PIN (not shown as you type)
    python manage.py pin_check someone@example.com --clear-lockout
"""
from getpass import getpass

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

from bc.models import Membership, User
from core.models import PinAttempt
from core.services import pin_locked_out


class Command(BaseCommand):
    help = "Check one staff member's PIN against Vensoft and show why it fails (no secrets printed)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--clear-lockout", action="store_true", help="forget their recent wrong tries")

    def handle(self, email, clear_lockout=False, **opts):
        users = list(User.objects.filter(email__iexact=email.strip()))
        if not users:
            raise CommandError("No Vensoft user with that email.")
        for user in users:
            self.stdout.write(f"User #{user.id} {user.full_name}  active={user.is_active}")
            if clear_lockout:
                PinAttempt.objects.filter(user_id=user.id).delete()
                self.stdout.write("  Wrong tries cleared.")
            since = timezone.now() - timezone.timedelta(minutes=settings.PIN_LOCKOUT_MINUTES)
            recent = PinAttempt.objects.filter(user_id=user.id, at__gte=since).count()
            self.stdout.write(f"  Wrong tries in the last {settings.PIN_LOCKOUT_MINUTES} min: {recent}"
                              f"{'  -> LOCKED OUT' if pin_locked_out(user) else ''}")

        pin = getpass("PIN (not shown): ").strip()
        self.stdout.write(f"  PIN entered: {len(pin)} digits, all digits={pin.isdigit()}")
        for user in users:
            rows = list(Membership.objects.filter(user_id=user.id))
            if not rows:
                self.stdout.write(f"  User #{user.id}: no membership rows")
            for m in rows:
                h = m.pin_hash or ""
                parts = h.split("$")
                kind = f"{parts[0]} ({parts[1]} rounds)" if len(parts) == 4 else (parts[0] if h else "EMPTY")
                try:
                    match = check_password(pin, h) if h else False
                except Exception as exc:  # e.g. a hasher whose library isn't installed
                    match = f"error: {type(exc).__name__}: {exc}"
                self.stdout.write(f"  Membership #{m.id} bowl={m.bowl_id} role={m.role} hash={kind} match={match}")

        # Other places a PIN might live (column names only)
        if connections["bowlcontrol"].vendor != "postgresql":
            return
        with connections["bowlcontrol"].cursor() as c:
            c.execute(
                "SELECT c.relname, a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND a.attnum > 0 AND NOT a.attisdropped AND a.attname ILIKE '%%pin%%' ORDER BY 1, 2"
            )
            cols = [f"{t}.{col}" for t, col in c.fetchall()]
        self.stdout.write("Columns named like 'pin' in bowlcontrol: " + (", ".join(cols) or "none"))
