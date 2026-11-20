"""Event Tools > Athlete Head Shot: MyDay lists events/bowlers from Mission Control and sends the photo there."""
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from core import headshots
from tests.test_myday import Base

EVENT = {"code": "632", "title": "Junior Classic Cup", "bowlers": [
    {"no": 1, "surname": "DOUGLASS", "first": "MORTY", "team": 1, "teamName": "TEAM 1", "master": 0, "photo": None},
    {"no": 2, "surname": "NISH", "first": "CALEB", "team": 1, "teamName": "TEAM 1", "master": 0,
     "photo": {"id": "N-NISH-CALEB", "url": "/api/headshots?photo=N-NISH-CALEB", "at": "2026-11-20T07:00:00Z", "by": "Sam"}},
]}
JPEG = b"\xff\xd8" + b"0" * 2000


class HeadShotTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = self.signed_in(); self.ctx.enable(); self.addCleanup(self.ctx.disable)
        self.unlock()

    def test_event_tools_button_and_menu(self):
        self.assertContains(self.client.get("/myday"), "Event Tools")
        self.assertContains(self.client.get("/events"), "Athlete Head Shot")

    @override_settings(EVENT_TOOLS_ENABLED=False)
    def test_switched_off(self):
        self.assertNotContains(self.client.get("/myday"), "Event Tools")
        self.assertEqual(self.client.get("/events").status_code, 404)

    def test_events_list(self):
        with mock.patch.object(headshots, "events", return_value=[{"code": "632", "title": "Junior Classic Cup", "changed": 1}]):
            resp = self.client.get("/events/headshots")
        self.assertContains(resp, "Junior Classic Cup")
        self.assertContains(resp, "/events/headshots/632")

    def test_mc_down_shows_a_message(self):
        with mock.patch.object(headshots, "events", side_effect=headshots.McError("Can't reach Mission Control right now.")):
            self.assertContains(self.client.get("/events/headshots"), "reach Mission Control")

    def test_bowler_list_shows_who_has_a_photo(self):
        with mock.patch.object(headshots, "event", return_value=json.loads(json.dumps(EVENT))):
            resp = self.client.get("/events/headshots/632")
        self.assertContains(resp, "DOUGLASS")
        self.assertContains(resp, "/events/headshots/photo/N-NISH-CALEB")
        self.assertContains(resp, "1 of 2")

    def test_upload_goes_to_mission_control(self):
        with mock.patch.object(headshots, "event", return_value=json.loads(json.dumps(EVENT))), \
             mock.patch.object(headshots, "_json", return_value={"ok": True, "photo": {"id": "N-DOUGLASS-MORTY", "at": "x"}}) as call:
            resp = self.client.post("/events/headshots/632/1", {"photo": SimpleUploadedFile("h.jpg", JPEG, "image/jpeg")},
                                    HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.json()["next"], "/events/headshots/632?done=1")
        body = json.loads(call.call_args.args[1])
        self.assertEqual((body["code"], body["no"], body["by"]), ("632", 1, "Sam Mason"))

    def test_not_a_jpeg_is_refused(self):
        with mock.patch.object(headshots, "event", return_value=json.loads(json.dumps(EVENT))), \
             mock.patch.object(headshots, "_json") as call:
            resp = self.client.post("/events/headshots/632/1", {"photo": SimpleUploadedFile("h.png", b"\x89PNG" * 100)},
                                    HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(resp.status_code, 502)
        call.assert_not_called()

    def test_unknown_bowler(self):
        with mock.patch.object(headshots, "event", return_value=json.loads(json.dumps(EVENT))):
            self.assertEqual(self.client.get("/events/headshots/632/9").status_code, 404)

    def test_bad_photo_id(self):
        self.assertEqual(self.client.get("/events/headshots/photo/..%2Fx").status_code, 404)
