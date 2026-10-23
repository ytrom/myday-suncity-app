from datetime import time, timedelta
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.utils import timezone

from bc.models import (
    BowlNote,
    BowlNoteAck,
    LeaveRequest,
    Membership,
    Shift,
    ShiftAssignment,
    TechTask,
    TechTaskAssignee,
    TimeEntry,
    User,
)
from core.models import SiteCheck

VENUE = {"lat": "-26.6721", "lng": "153.1061"}
FAR_AWAY = {"lat": "-27.4698", "lng": "153.0251"}  # Brisbane CBD


class Base(TestCase):
    databases = {"default", "bowlcontrol"}

    def setUp(self):
        self.sam = User.objects.create(
            email="sam@suncitytenpin.com.au", first_name="Sam", last_name="Mason",
            is_active=True, is_staff=True,
        )
        Membership.objects.create(user=self.sam, role="staff", pin_hash=make_password("1234"), bowl_id=1)

    def signed_in(self, email="sam@suncitytenpin.com.au"):
        return override_settings(DEV_USER_EMAIL=email)

    def unlock(self, pin="1234"):
        return self.client.post("/unlock", {"pin": pin})


class IdentityTests(Base):
    def test_no_identity_is_refused(self):
        self.assertEqual(self.client.get("/myday").status_code, 403)

    def test_unknown_email_is_refused(self):
        with self.signed_in("stranger@example.com"):
            resp = self.client.get("/myday")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "not on the staff list", status_code=403)

    def test_inactive_staff_is_refused(self):
        User.objects.filter(id=self.sam.id).update(is_active=False)
        with self.signed_in():
            self.assertEqual(self.client.get("/myday").status_code, 403)

    def test_test_user_is_ignored_for_requests_through_cloudflare(self):
        with self.signed_in():
            resp = self.client.get("/myday", HTTP_CF_RAY="abc123-SYD")
        self.assertEqual(resp.status_code, 403)

    def test_health_needs_no_sign_in(self):
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})


@override_settings(CF_ACCESS_TEAM_DOMAIN="suncity.cloudflareaccess.com", CF_ACCESS_AUD="aud-123")
class CloudflareTokenTests(Base):
    def setUp(self):
        super().setUp()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        signing_key = mock.Mock(key=self.key.public_key())
        client = mock.Mock(get_signing_key_from_jwt=mock.Mock(return_value=signing_key))
        patcher = mock.patch("core.middleware._jwks_client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def token(self, **overrides):
        claims = {
            "email": "sam@suncitytenpin.com.au",
            "aud": ["aud-123"],
            "iss": "https://suncity.cloudflareaccess.com",
            "exp": timezone.now() + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256")

    def test_valid_token_reaches_pin_lock(self):
        resp = self.client.get("/myday", HTTP_CF_ACCESS_JWT_ASSERTION=self.token())
        self.assertRedirects(resp, "/unlock", fetch_redirect_response=False)

    def test_wrong_audience_is_refused(self):
        resp = self.client.get("/myday", HTTP_CF_ACCESS_JWT_ASSERTION=self.token(aud=["other-app"]))
        self.assertEqual(resp.status_code, 403)

    def test_expired_token_is_refused(self):
        token = self.token(exp=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get("/myday", HTTP_CF_ACCESS_JWT_ASSERTION=token).status_code, 403)

    def test_test_user_setting_is_ignored_once_cloudflare_is_configured(self):
        with self.signed_in():
            self.assertEqual(self.client.get("/myday").status_code, 403)


class PinLockTests(Base):
    def test_app_opens_on_pin_pad(self):
        with self.signed_in():
            resp = self.client.get("/myday")
            self.assertRedirects(resp, "/unlock", fetch_redirect_response=False)
            self.assertContains(self.client.get("/unlock"), "Welcome back, Sam")

    def test_right_pin_opens_app(self):
        with self.signed_in():
            self.assertRedirects(self.unlock(), "/myday", fetch_redirect_response=False)
            resp = self.client.get("/myday")
        self.assertContains(resp, "Sam")
        self.assertContains(resp, "Log on")

    def test_wrong_pin_is_refused(self):
        with self.signed_in():
            resp = self.unlock("9999")
            self.assertContains(resp, "PIN didn", status_code=400)
            self.assertRedirects(self.client.get("/myday"), "/unlock", fetch_redirect_response=False)

    def test_lockout_after_too_many_tries(self):
        with self.signed_in():
            for _ in range(5):
                self.unlock("0000")
            resp = self.unlock("1234")
        self.assertContains(resp, "Too many tries", status_code=400)

    def test_locks_again_after_idle(self):
        with self.signed_in():
            self.unlock()
            session = self.client.session
            session["unlocked_until"] = timezone.now().timestamp() - 1
            session.save()
            self.assertRedirects(self.client.get("/myday"), "/unlock", fetch_redirect_response=False)

    def test_htmx_requests_are_sent_to_pin_pad_too(self):
        with self.signed_in():
            resp = self.client.get("/roster", HTTP_HX_REQUEST="true")
        self.assertEqual(resp["HX-Redirect"], "/unlock")


class ClockTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = self.signed_in()
        self.ctx.enable()
        self.addCleanup(self.ctx.disable)
        self.unlock()

    def test_log_on_needs_the_pin(self):
        resp = self.client.post("/clock/on", {"pin": "1111", **VENUE})
        self.assertContains(resp, "PIN didn")
        self.assertEqual(resp["HX-Retarget"], "#shift-actions")
        self.assertFalse(SiteCheck.objects.exists())

    def test_on_site_log_on_in_test_mode_saves_nothing_to_vensoft(self):
        resp = self.client.post("/clock/on", {"pin": "1234", **VENUE})
        self.assertContains(resp, "On site")
        self.assertContains(resp, "Log off")
        self.assertEqual(TimeEntry.objects.count(), 0)
        check = SiteCheck.objects.get()
        self.assertTrue(check.on_site)
        self.assertEqual(check.reason, "")

    def test_venue_wifi_counts_as_on_site_without_location(self):
        resp = self.client.post("/clock/on", {"pin": "1234"}, HTTP_CF_CONNECTING_IP="203.0.113.10")
        self.assertContains(resp, "On site")

    def test_blocked_location_counts_as_off_site(self):
        resp = self.client.post("/clock/on", {"pin": "1234"})
        self.assertContains(resp, "working off-site today")

    def test_off_site_log_on_asks_why_and_keeps_only_the_reason(self):
        resp = self.client.post("/clock/on", {"pin": "1234", **FAR_AWAY})
        self.assertContains(resp, "You're not at Sun City right now")
        self.assertFalse(SiteCheck.objects.exists())

        resp = self.client.post("/clock/on/offsite", {"reason": "Picking up stock"})
        self.assertContains(resp, "Off site")
        check = SiteCheck.objects.get()
        self.assertFalse(check.on_site)
        self.assertEqual(check.reason, "Picking up stock")
        stored = {f.name for f in SiteCheck._meta.get_fields()}
        self.assertFalse(stored & {"lat", "lng", "latitude", "longitude", "distance"})

    def test_off_site_answer_needs_a_recent_pin(self):
        resp = self.client.post("/clock/on/offsite", {"reason": "Sneaky"})
        self.assertContains(resp, "took too long")
        self.assertFalse(SiteCheck.objects.exists())

    @override_settings(WRITES_ENABLED=True)
    def test_log_on_and_off_write_the_login_clock_when_enabled(self):
        self.client.post("/clock/on", {"pin": "1234", **VENUE})
        entry = TimeEntry.objects.get()
        self.assertIsNone(entry.clock_out)
        self.assertEqual(entry.source, "myday")
        self.assertEqual(entry.bowl_id, 1)

        self.client.post("/clock/off", {"pin": "1234", **FAR_AWAY})
        self.client.post("/clock/off/offsite", {"reason": "Bank run"})
        entry.refresh_from_db()
        self.assertIsNotNone(entry.clock_out)
        self.assertIn("Off site: Bank run", entry.note)


class TabTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = self.signed_in()
        self.ctx.enable()
        self.addCleanup(self.ctx.disable)
        self.unlock()
        now = timezone.now()
        self.task = TechTask.objects.create(title="Print league sheets", status="OPEN", created_at=now)
        TechTaskAssignee.objects.create(techtask=self.task, user=self.sam)
        other = User.objects.create(email="x@y.z", first_name="Alex", last_name="Lee", is_active=True, is_staff=True)
        hidden = TechTask.objects.create(title="Someone else's job", status="OPEN", created_at=now)
        TechTaskAssignee.objects.create(techtask=hidden, user=other)
        shift = Shift.objects.create(
            role="Front desk", date=timezone.localdate() + timedelta(days=1),
            start_time=time(9), end_time=time(15),
        )
        self.assignment = ShiftAssignment.objects.create(shift=shift, user=self.sam, confirmed=False)
        self.note = BowlNote.objects.create(
            title="League night starts 6:30", note="Sheets out by 6.", target_all_staff=True,
            requires_ack=True, publish_at=now - timedelta(hours=1),
        )
        BowlNote.objects.create(
            title="Expired", note="Old", target_all_staff=True, requires_ack=False,
            publish_at=now - timedelta(days=9), expires_at=now - timedelta(days=1),
        )

    def test_myday_shows_my_tasks_and_unread_banner(self):
        resp = self.client.get("/myday")
        self.assertContains(resp, "Print league sheets")
        self.assertNotContains(resp, "Someone else's job")
        self.assertContains(resp, "1 new staff message")
        self.assertContains(resp, "1 to do")

    def test_tick_task_in_test_mode(self):
        resp = self.client.post(f"/tasks/{self.task.id}/toggle")
        self.assertContains(resp, 'aria-pressed="true"')
        self.assertEqual(resp["HX-Trigger"], "counts-changed")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "OPEN")
        self.assertContains(self.client.get("/myday"), "0 to do")

    @override_settings(WRITES_ENABLED=True)
    def test_tick_task_saves_when_enabled(self):
        self.client.post(f"/tasks/{self.task.id}/toggle")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "DONE")
        self.assertEqual(self.task.completed_by_id, self.sam.id)

    def test_cannot_tick_someone_elses_task(self):
        hidden = TechTask.objects.get(title="Someone else's job")
        self.assertEqual(self.client.post(f"/tasks/{hidden.id}/toggle").status_code, 404)

    def test_roster_and_confirm(self):
        resp = self.client.get("/roster")
        self.assertContains(resp, "9:00 a.m.")
        self.assertContains(resp, "1 to confirm")
        resp = self.client.post(f"/roster/{self.assignment.id}/confirm")
        self.assertContains(resp, "Confirmed")

    @override_settings(WRITES_ENABLED=True)
    def test_confirm_saves_when_enabled(self):
        self.client.post(f"/roster/{self.assignment.id}/confirm")
        self.assignment.refresh_from_db()
        self.assertTrue(self.assignment.confirmed)

    def test_messages_hide_expired_and_ack(self):
        resp = self.client.get("/messages")
        self.assertContains(resp, "League night starts 6:30")
        self.assertNotContains(resp, "Expired")
        resp = self.client.post(f"/messages/{self.note.id}/ack")
        self.assertContains(resp, "Read")
        self.assertFalse(BowlNoteAck.objects.exists())

    @override_settings(WRITES_ENABLED=True)
    def test_ack_saves_read_receipt_when_enabled(self):
        self.client.post(f"/messages/{self.note.id}/ack")
        self.client.post(f"/messages/{self.note.id}/ack")
        self.assertEqual(BowlNoteAck.objects.filter(note=self.note, user=self.sam).count(), 1)

    def test_rdo_request_in_test_mode(self):
        start = (timezone.localdate() + timedelta(days=20)).isoformat()
        resp = self.client.post("/rdo", {"type": "rdo", "start": start, "end": start})
        self.assertContains(resp, "Test mode")
        self.assertFalse(LeaveRequest.objects.exists())

    @override_settings(WRITES_ENABLED=True)
    def test_rdo_request_saves_when_enabled(self):
        start = timezone.localdate() + timedelta(days=20)
        resp = self.client.post("/rdo", {"type": "rdo", "start": start.isoformat(), "reason": "Wedding"})
        self.assertContains(resp, "Request sent")
        req = LeaveRequest.objects.get()
        self.assertEqual((req.type, req.status, req.start, req.end), ("rdo", "pending", start, start))
        self.assertContains(self.client.get("/rdo"), "1 pending")

    def test_rdo_rejects_bad_dates(self):
        start = timezone.localdate() + timedelta(days=5)
        resp = self.client.post("/rdo", {"type": "rdo", "start": start.isoformat(), "end": (start - timedelta(days=1)).isoformat()})
        self.assertContains(resp, "Check the dates")

    def test_pwa_files(self):
        self.assertEqual(self.client.get("/manifest.webmanifest").json()["short_name"], "MyDay")
        self.assertEqual(self.client.get("/sw.js")["Service-Worker-Allowed"], "/")


class DetailsTests(Base):
    def setUp(self):
        super().setUp()
        User.objects.filter(id=self.sam.id).update(phone_number="0400 111 222")
        self.ctx = self.signed_in()
        self.ctx.enable()
        self.addCleanup(self.ctx.disable)
        self.unlock()

    def form(self, **overrides):
        data = {"phone": "0400 111 222", "address": "1 Lane St, Buderim", "emergency_name": "Alex Mason",
                "emergency_phone": "0400 333 444", "emergency_relationship": "Partner"}
        data.update(overrides)
        return data

    def test_avatar_opens_my_details_with_vensoft_phone(self):
        self.assertContains(self.client.get("/myday"), 'href="/me"')
        resp = self.client.get("/me")
        self.assertContains(resp, "sam@suncitytenpin.com.au")
        self.assertContains(resp, 'value="0400 111 222"')
        self.assertContains(resp, "Lock MyDay")

    def test_saving_sends_to_the_office_without_touching_vensoft(self):
        resp = self.client.post("/me", self.form())
        self.assertContains(resp, "sent to the office")
        from core.models import DetailsUpdate
        update = DetailsUpdate.objects.get()
        self.assertEqual((update.address, update.emergency_relationship), ("1 Lane St, Buderim", "Partner"))
        self.assertEqual(User.objects.get(id=self.sam.id).phone_number, "0400 111 222")
        self.assertContains(self.client.get("/me"), 'value="Alex Mason"')

    def test_saving_the_same_again_does_nothing(self):
        self.client.post("/me", self.form())
        self.assertContains(self.client.post("/me", self.form()), "Nothing changed")
        from core.models import DetailsUpdate
        self.assertEqual(DetailsUpdate.objects.count(), 1)

    def test_bad_phone_and_missing_contact_name(self):
        self.assertContains(self.client.post("/me", self.form(phone="call me")), "Check the phone numbers")
        self.assertContains(self.client.post("/me", self.form(emergency_name="")), "emergency contact&#x27;s name")

    def test_office_command_lists_and_marks_done(self):
        from io import StringIO
        from django.core.management import call_command
        from core.models import DetailsUpdate
        self.client.post("/me", self.form())
        out = StringIO()
        call_command("staff_details", stdout=out)
        self.assertIn("Sam Mason", out.getvalue())
        call_command("staff_details", done=DetailsUpdate.objects.get().id, stdout=StringIO())
        out = StringIO()
        call_command("staff_details", stdout=out)
        self.assertIn("Nothing waiting", out.getvalue())


class LongPinTests(Base):
    def test_six_digit_pin_opens_app(self):
        Membership.objects.filter(user=self.sam).update(pin_hash=make_password("246810"))
        with self.signed_in():
            self.assertContains(self.client.get("/unlock"), 'data-pin-max="6"')
            self.assertRedirects(self.unlock("246810"), "/myday", fetch_redirect_response=False)


class FastPinTests(Base):
    def fast(self, pin, salt="aB3dE5"):
        import hashlib
        return "fast$" + salt + "$" + hashlib.sha256((salt + pin).encode()).hexdigest()

    def test_fast_sha256_pin_opens_app(self):
        Membership.objects.filter(user=self.sam).update(pin_hash=self.fast("135790"))
        with self.signed_in():
            self.assertRedirects(self.unlock("135790"), "/myday", fetch_redirect_response=False)

    def test_wrong_fast_pin_is_refused(self):
        Membership.objects.filter(user=self.sam).update(pin_hash=self.fast("135790"))
        with self.signed_in():
            self.assertEqual(self.unlock("135791").status_code, 400)

    def test_scheme_is_reported_and_can_be_pinned(self):
        import base64, hashlib, hmac
        from core.pins import describe, match_fast
        h = "fast$salt$" + base64.b64encode(hmac.new(b"salt", b"2468", hashlib.sha256).digest()).decode()
        self.assertEqual(match_fast("2468", h), "hmac(salt,pin)/b64")
        self.assertTrue(describe(h).startswith("fast [4 hex]") or describe(h).startswith("fast [4 b64]"))
        with mock.patch.dict("os.environ", {"MYDAY_FAST_PIN_SCHEME": "sha256(salt+pin)/hex"}):
            self.assertIsNone(match_fast("2468", h))
