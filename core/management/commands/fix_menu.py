"""Show the Mechanic "What was fixed?" list as MyDay sees it, and where it comes from.

    python manage.py fix_menu

It's read from Vensoft (Tech Settings: issue options and their sub-items). A sub-item named
"Back - Pin in the door" shows as Ball stuck > Back > Pin in the door.
"""
from django.core.management.base import BaseCommand

from core import lanes


class Command(BaseCommand):
    help = "Print the Mechanic fix list (Vensoft issue options and sub-items)."

    def add_arguments(self, parser):
        parser.add_argument("--bowl", type=int, default=None, help="Vensoft bowl id (default: all)")

    def handle(self, bowl=None, **opts):
        try:
            menu, source = lanes._vensoft_fix_menu(bowl), "Vensoft"
        except Exception as e:
            self.stdout.write(f"Can't read Vensoft's fault list: {e}")
            self.stdout.write("Run on VM 102 in bowlcontrol: GRANT SELECT ON tech_issueoption, tech_issuesuboption TO myday_app;")
            menu, source = [], ""
        if not menu:
            menu, source = lanes._lane_marshal_fix_menu(), "Lane Marshal (fallback)"
        self.stdout.write(f"Fix list from {source}:")
        for issue in menu:
            self.stdout.write(f"  {issue['name']}  [id {issue['id']}]")
            for c in lanes.choices_for(issue["options"]):
                if c.get("group"):
                    self.stdout.write(f"    {c['group']} >")
                    for item in c["items"]:
                        self.stdout.write(f"      {item['label']}")
                else:
                    self.stdout.write(f"    {c['label']}")
