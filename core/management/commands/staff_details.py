"""List the details staff sent from My details, and mark them done once copied into Vensoft.

    python manage.py staff_details            # waiting for the office
    python manage.py staff_details --all      # everything
    python manage.py staff_details --done 12  # mark update 12 as copied
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bc.models import User
from core.models import DetailsUpdate


class Command(BaseCommand):
    help = "List staff details updates sent from MyDay, or mark one as copied into Vensoft."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="include updates already copied")
        parser.add_argument("--done", type=int, metavar="ID", help="mark this update as copied")

    def handle(self, *args, **opts):
        if opts["done"]:
            if not DetailsUpdate.objects.filter(id=opts["done"]).update(done_at=timezone.now()):
                raise CommandError(f"No update {opts['done']}.")
            self.stdout.write(f"Update {opts['done']} marked as done.")
            return
        updates = DetailsUpdate.objects.all() if opts["all"] else DetailsUpdate.objects.filter(done_at__isnull=True)
        names = {u.id: u.full_name for u in User.objects.filter(id__in={d.user_id for d in updates})}
        if not updates:
            self.stdout.write("Nothing waiting.")
        for d in updates:
            state = f"done {timezone.localtime(d.done_at):%d %b}" if d.done_at else "waiting"
            self.stdout.write(
                f"#{d.id}  {names.get(d.user_id, d.user_id)}  sent {timezone.localtime(d.sent_at):%d %b %H:%M}  ({state})\n"
                f"    Mobile: {d.phone or '-'}\n    Address: {d.address or '-'}\n"
                f"    Emergency: {d.emergency_name or '-'} ({d.emergency_relationship or '-'}) {d.emergency_phone or ''}\n"
            )
