"""MyDay's own data, kept in its local database on VM 106 (not in BowlControl)."""
from django.db import models


class PinAttempt(models.Model):
    """Failed PIN tries, for the short lockout after too many wrong PINs."""

    user_id = models.IntegerField(db_index=True)
    at = models.DateTimeField(auto_now_add=True)


class SiteCheck(models.Model):
    """Result of the on-site check at Log on / Log off.

    Only on site / off site and the person's reason are kept: never coordinates or distance.
    """

    ACTIONS = [("on", "Log on"), ("off", "Log off")]

    user_id = models.IntegerField(db_index=True)
    action = models.CharField(max_length=3, choices=ACTIONS)
    on_site = models.BooleanField(null=True)  # None: location check switched off for this person
    reason = models.CharField(max_length=200, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    time_entry_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]


class DetailsUpdate(models.Model):
    """Contact details a staff member sent from My details, waiting for the office.

    Kept here until we know where Vensoft keeps address and emergency contact; the office
    copies them across, then marks them done (manage.py staff_details).
    """

    user_id = models.IntegerField(db_index=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=300, blank=True)
    emergency_name = models.CharField(max_length=120, blank=True)
    emergency_phone = models.CharField(max_length=40, blank=True)
    emergency_relationship = models.CharField(max_length=60, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]


class StaffSetting(models.Model):
    """Per-person switches. location_check: ask for the phone's location at Log on / Log off.
    mechanic: show the Mechanic screen (lane trouble calls)."""

    user_id = models.IntegerField(unique=True)
    location_check = models.BooleanField(default=False)
    mechanic = models.BooleanField(default=False)
