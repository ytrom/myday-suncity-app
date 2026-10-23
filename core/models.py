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
    on_site = models.BooleanField()
    reason = models.CharField(max_length=200, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    time_entry_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]
