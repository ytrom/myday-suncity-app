"""Build demo BowlControl tables for a local preview: DJANGO_SETTINGS_MODULE=tests.smoke_settings python -m tests.seed"""
from datetime import time, timedelta

import django

django.setup()

from django.apps import apps  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402
from django.db import connections  # noqa: E402
from django.utils import timezone  # noqa: E402

from bc.models import (  # noqa: E402
    BowlNote, Membership, Shift, ShiftAssignment, TechTask, TechTaskAssignee, User,
)

with connections["bowlcontrol"].schema_editor() as editor:
    for model in apps.get_app_config("bc").get_models():
        model._meta.managed = True
        editor.create_model(model)

now = timezone.now()
today = timezone.localdate()
sam = User.objects.create(email="sam@suncitytenpin.com.au", first_name="Sam", last_name="Mason", is_active=True, is_staff=True)
Membership.objects.create(user=sam, role="staff", pin_hash=make_password("1234"), bowl_id=1)
for title in ["Open lanes 1-12 and check pinsetters", "Print league sheets for Tuesday Mixed", "Restock shoe spray"]:
    t = TechTask.objects.create(title=title, status="OPEN", created_at=now)
    TechTaskAssignee.objects.create(techtask=t, user=sam)
TechTask.objects.create(title="Wipe down bar fridges", status="OPEN", created_at=now)
for days, role, s, e in [(0, "Front desk", 9, 15), (1, "Front desk", 16, 22), (3, "Bar", 17, 23), (6, "Leagues", 18, 22)]:
    sh = Shift.objects.create(role=role, date=today + timedelta(days=days), start_time=time(s), end_time=time(e))
    ShiftAssignment.objects.create(shift=sh, user=sam, confirmed=days < 2)
BowlNote.objects.create(title="League night starts 6:30 this week", note="Sheets out by 6. Lanes 13-24 reserved.",
                        target_all_staff=True, requires_ack=True, publish_at=now - timedelta(hours=2))
BowlNote.objects.create(title="New shoe spray supplier", note="Boxes are in the back store room.",
                        target_all_staff=True, requires_ack=False, publish_at=now - timedelta(days=2))
print("seeded")
