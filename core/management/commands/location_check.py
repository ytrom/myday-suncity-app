"""Turn the Log on / Log off location check on or off for one person.

    python manage.py location_check                          # who has it on
    python manage.py location_check someone@example.com on
    python manage.py location_check someone@example.com off
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bc.models import User
from core import services
from core.models import StaffSetting


class Command(BaseCommand):
    help = "Switch the on-site location check at Log on / Log off on or off per person."

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?")
        parser.add_argument("state", nargs="?", choices=["on", "off"])

    def handle(self, email=None, state=None, **opts):
        if email:
            user = User.objects.filter(email__iexact=email.strip()).first()
            if not user:
                raise CommandError("No Vensoft user with that email.")
            if state:
                services.set_location_check(user, state == "on")
            self.stdout.write(f"{user.full_name}: location check {'on' if services.location_check_on(user) else 'off'}")
            return
        default = "on" if settings.LOCATION_CHECK_DEFAULT else "off"
        self.stdout.write(f"Everyone else: {default}")
        names = {u.id: u.full_name for u in User.objects.all()}
        for s in StaffSetting.objects.all():
            self.stdout.write(f"  {names.get(s.user_id, s.user_id)}: {'on' if s.location_check else 'off'}")
