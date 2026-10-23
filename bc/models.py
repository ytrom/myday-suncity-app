"""Read-mostly mirrors of BowlControl (Vensoft) tables.

These are unmanaged: MyDay never creates, migrates or drops them. Columns are the ones
Seawoz mapped on 23 Oct 2026 (MyDay-Steps-1-3-Report.md). Fletcher owns the real models;
if a column here disagrees with BowlControl, BowlControl wins and this file is wrong.
"""
from django.db import models

FK = dict(on_delete=models.DO_NOTHING, db_constraint=False)


class User(models.Model):
    email = models.CharField(max_length=254)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=40, null=True)
    is_active = models.BooleanField()
    is_staff = models.BooleanField()

    class Meta:
        managed = False
        db_table = "accounts_user"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self):
        return ((self.first_name or "?")[:1] + (self.last_name or "")[:1]).upper()


class Membership(models.Model):
    user = models.ForeignKey(User, related_name="memberships", **FK)
    role = models.CharField(max_length=18)
    pin_hash = models.CharField(max_length=128, null=True)
    bowl_id = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = "accounts_membership"


class TimeEntry(models.Model):
    """The login clock. clock_out NULL means the person is clocked in."""

    user = models.ForeignKey(User, related_name="time_entries", **FK)
    bowl_id = models.IntegerField(null=True)
    clock_in = models.DateTimeField()
    clock_out = models.DateTimeField(null=True)
    source = models.CharField(max_length=40, null=True)
    note = models.TextField(null=True)
    payable_minutes = models.IntegerField(null=True)
    is_locked = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = "core_timeentry"


class BowlNote(models.Model):
    """Staff messages shown on the login clock."""

    title = models.CharField(max_length=200)
    note = models.TextField()
    target_all_staff = models.BooleanField()
    requires_ack = models.BooleanField()
    publish_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    assigned_user = models.ForeignKey(User, null=True, related_name="+", **FK)

    class Meta:
        managed = False
        db_table = "core_bowlnote"


class BowlNoteAck(models.Model):
    note = models.ForeignKey(BowlNote, related_name="acks", **FK)
    user = models.ForeignKey(User, related_name="+", **FK)
    acknowledged_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "core_bowlnoteack"


class Shift(models.Model):
    role = models.CharField(max_length=60, null=True)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    period_id = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = "roster_shift"


class ShiftAssignment(models.Model):
    shift = models.ForeignKey(Shift, related_name="assignments", **FK)
    user = models.ForeignKey(User, related_name="+", **FK)
    confirmed = models.BooleanField()

    class Meta:
        managed = False
        db_table = "roster_shiftassignment"


class LeaveRequest(models.Model):
    # type/status values are still to be confirmed with Fletcher.
    user = models.ForeignKey(User, related_name="+", **FK)
    start = models.DateField()
    end = models.DateField()
    type = models.CharField(max_length=40)
    status = models.CharField(max_length=40)
    reason = models.TextField(null=True)
    decided_by = models.ForeignKey(User, null=True, related_name="+", **FK)

    class Meta:
        managed = False
        db_table = "roster_leaverequest"


class TechTask(models.Model):
    """Tech/venue tasks (the same table Mission Control's My Day reads)."""

    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20)
    department = models.CharField(max_length=20, null=True)
    area = models.CharField(max_length=60, null=True)
    lane_label = models.CharField(max_length=40, null=True)
    origin = models.CharField(max_length=20, null=True)
    priority = models.CharField(max_length=20, null=True)
    description = models.TextField(null=True)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey(User, null=True, related_name="+", **FK)
    completed_by = models.ForeignKey(User, null=True, related_name="+", **FK)

    class Meta:
        managed = False
        db_table = "tech_techtask"


class TechTaskAssignee(models.Model):
    techtask = models.ForeignKey(TechTask, related_name="assignee_links", **FK)
    user = models.ForeignKey(User, related_name="+", **FK)

    class Meta:
        managed = False
        db_table = "tech_techtask_assignees"
