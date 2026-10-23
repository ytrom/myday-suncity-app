"""Give or take away the Mechanic screen (lane trouble calls) for one person.

    python manage.py mechanic                          # who has it
    python manage.py mechanic someone@example.com on
    python manage.py mechanic someone@example.com off
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bc.models import User
from core import services
from core.models import StaffSetting


class Command(BaseCommand):
    help = "Switch the Mechanic screen on or off per person."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?")
        parser.add_argument("state", nargs="?", choices=["on", "off"])

    def handle(self, email=None, state=None, **opts):
        if not settings.MECHANIC_ENABLED:
            self.stdout.write("Note: MYDAY_MECHANIC is off in .env, so nobody sees the screen yet.")
        if email:
            user = User.objects.filter(email__iexact=email.strip()).first()
            if not user:
                raise CommandError("No Vensoft user with that email.")
            if state:
                services.set_mechanic(user, state == "on")
            on = StaffSetting.objects.filter(user_id=user.id, mechanic=True).exists()
            self.stdout.write(f"{user.full_name}: mechanic {'on' if on else 'off'}")
            return
        names = {u.id: u.full_name for u in User.objects.all()}
        techs = [names.get(s.user_id, s.user_id) for s in StaffSetting.objects.filter(mechanic=True)]
        self.stdout.write("Mechanic screen: " + (", ".join(map(str, techs)) or "nobody yet"))
