"""Call alerts: Web Push to the techs' phones when a new lane call comes in."""
import json
import struct
from io import StringIO
from unittest import mock

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import override_settings

from bc.models import Membership, User
from core import services, webpush
from core.lanes import Call
from core.management.commands.call_alerts import Command
from core.models import PushSubscription

from .test_myday import Base


def phone_keys():
    """A pretend phone: its private key, and the p256dh/auth its browser would hand over."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, webpush.b64url(raw), webpush.b64url(b"0123456789abcdef")


def decrypt(body, key, p256dh, auth):
    """What the phone's browser does with a push (RFC 8291), to check our side of it."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    secret = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = webpush._hkdf(webpush.unb64url(auth), secret,
                        b"WebPush: info\x00" + webpush.unb64url(p256dh) + as_public, 32)
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


class WebPushTests(Base):
    def test_phone_can_read_the_alert(self):
        key, p256dh, auth = phone_keys()
        body = webpush.encrypt(b'{"title": "Lane 4"}', p256dh, auth)
        self.assertEqual(decrypt(body, key, p256dh, auth), b'{"title": "Lane 4"}')

    def test_matches_the_standard(self):
        """RFC 8291 appendix A: the same keys and salt must give the same bytes."""
        as_key = ec.derive_private_key(
            int.from_bytes(webpush.unb64url("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
        with mock.patch("core.webpush.ec.generate_private_key", return_value=as_key), \
                mock.patch("core.webpush.os.urandom", return_value=webpush.unb64url("DGv6ra1nlYgDCS1FRnbzlw")):
            body = webpush.encrypt(
                b"When I grow up, I want to be a watermelon",
                "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                "BTBZMqHH6r4Tts7J_aSIgg")
        self.assertEqual(webpush.b64url(body),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6"
                         "TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy"
                         "4a-fN")

    def test_request_is_signed_with_our_key(self):
        header = webpush._vapid_header("https://fcm.googleapis.com/fcm/send/abc")
        token, k = header.removeprefix("vapid t=").split(", k=")
        self.assertEqual(k, webpush.public_key())
        claims = jwt.decode(token, webpush.vapid_key().public_key(), algorithms=["ES256"],
                            audience="https://fcm.googleapis.com")
        self.assertTrue(claims["sub"].startswith("mailto:"))

    def test_key_is_kept(self):
        self.assertEqual(webpush.public_key(), webpush.public_key())

    def test_gone_phones_are_forgotten(self):
        _, p256dh, auth = phone_keys()
        gone = PushSubscription.objects.create(user_id=1, endpoint="https://push.example/gone", p256dh=p256dh, auth=auth)
        ok = PushSubscription.objects.create(user_id=1, endpoint="https://push.example/ok", p256dh=p256dh, auth=auth)
        with mock.patch("core.webpush.send", side_effect=lambda s, m: 410 if s == gone else 201):
            self.assertEqual(webpush.send_all([gone, ok], {"title": "x"}), 1)
        self.assertEqual(list(PushSubscription.objects.values_list("endpoint", flat=True)), [ok.endpoint])

    def test_service_worker_shows_the_alert(self):
        js = self.client.get("/sw.js").content.decode()
        self.assertIn("showNotification", js)
        self.assertIn("notificationclick", js)


@override_settings(MECHANIC_ENABLED=True)
class AlertsScreenTests(Base):
    def setUp(self):
        super().setUp()
        self.ctx = self.signed_in()
        self.ctx.enable()
        self.addCleanup(self.ctx.disable)
        self.unlock()
        patcher = mock.patch("core.lanes.open_calls", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        _, self.p256dh, self.auth = phone_keys()
        self.sub = {"endpoint": "https://push.example/sam", "keys": {"p256dh": self.p256dh, "auth": self.auth}}

    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type="application/json")

    def test_only_for_mechanics(self):
        self.assertEqual(self.post("/mechanic/alerts", self.sub).status_code, 404)
        self.assertFalse(PushSubscription.objects.exists())

    def test_turn_on_and_off(self):
        services.set_mechanic(self.sam, True)
        self.assertContains(self.client.get("/mechanic"), "Turn on call alerts")
        self.assertEqual(self.post("/mechanic/alerts", self.sub).json(), {"on": True})
        self.post("/mechanic/alerts", self.sub)  # every visit sends it again: still one phone
        self.assertEqual(PushSubscription.objects.get().user_id, self.sam.id)
        self.assertEqual(self.post("/mechanic/alerts", {"endpoint": self.sub["endpoint"], "off": True}).json(), {"on": False})
        self.assertFalse(PushSubscription.objects.exists())

    def test_bad_subscription(self):
        services.set_mechanic(self.sam, True)
        self.assertEqual(self.post("/mechanic/alerts", {"endpoint": "http://x"}).status_code, 400)
        self.assertEqual(self.client.post("/mechanic/alerts", "nope", content_type="application/json").status_code, 400)

    def test_send_a_test(self):
        services.set_mechanic(self.sam, True)
        self.post("/mechanic/alerts", self.sub)
        with mock.patch("core.webpush.send", return_value=201) as send:
            self.assertEqual(self.client.post("/mechanic/alerts/test").json(), {"sent": 1})
        self.assertEqual(send.call_args[0][1], webpush.TEST_MESSAGE)


@override_settings(MECHANIC_ENABLED=True)
class CallAlertsJobTests(Base):
    def setUp(self):
        super().setUp()
        self.calls = [Call(7, "12", "Ball return", "", "", 20, None)]
        patcher = mock.patch("core.lanes.open_calls", side_effect=lambda: list(self.calls))
        patcher.start()
        self.addCleanup(patcher.stop)
        _, p256dh, auth = phone_keys()
        services.set_mechanic(self.sam, True)
        self.sam_phone = PushSubscription.objects.create(user_id=self.sam.id, endpoint="https://push.example/sam",
                                                         p256dh=p256dh, auth=auth)
        jo = User.objects.create(email="jo@suncitytenpin.com.au", first_name="Jo", last_name="Bell", is_active=True, is_staff=True)
        Membership.objects.create(user=jo, role="staff", bowl_id=1)
        PushSubscription.objects.create(user_id=jo.id, endpoint="https://push.example/jo", p256dh=p256dh, auth=auth)

    def test_rings_tech_phones_for_new_calls_only(self):
        job = Command(stdout=StringIO())
        with mock.patch("core.webpush.send", return_value=201) as send:
            seen = job.check(None)  # calls already open when the job starts don't ring
            self.assertEqual(send.call_count, 0)
            self.calls.append(Call(9, "3", "Full Set", "", "front row", 2, None))
            seen = job.check(seen)
            self.assertEqual(send.call_count, 1)  # Sam only: Jo isn't a tech
            sub, message = send.call_args[0]
            self.assertEqual(sub, self.sam_phone)
            self.assertEqual(message["title"], "Lane 3: Full Set")
            self.assertEqual(message["body"], "front row")
            job.check(seen)
            self.assertEqual(send.call_count, 1)  # nothing new

    def test_lane_database_down(self):
        job = Command(stdout=StringIO())
        with mock.patch("core.lanes.open_calls", side_effect=Exception("down")):
            self.assertEqual(job.check({7}), {7})

    def test_test_alert_from_the_command_line(self):
        out = StringIO()
        with mock.patch("core.webpush.send", return_value=201):
            Command(stdout=out).handle(test="sam@suncitytenpin.com.au")
        self.assertIn("sent to 1 of 1", out.getvalue())
