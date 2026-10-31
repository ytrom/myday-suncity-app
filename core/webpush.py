"""Web Push: lane-call alerts that reach a tech's phone even when MyDay is closed or the
phone is locked. The phone shows them as a normal notification, with its sound and buzz.

Each phone's browser gives us a push address (endpoint) and keys when the tech turns alerts
on. We encrypt the message for that phone (RFC 8291, aes128gcm) and sign the request with
MyDay's own VAPID key (RFC 8292), so no Google or Apple account is needed. The VAPID key is
made on first use and kept in the data volume: if it changes, every phone has to turn
alerts on again.
"""
import base64
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

log = logging.getLogger(__name__)

TTL_SECONDS = 120  # a call alert older than this isn't worth showing
GONE = (404, 410)  # the phone turned alerts off or the browser dropped the address


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_key():
    """MyDay's signing key, made once and kept in the data folder."""
    path = settings.DATA_DIR / "vapid_private.pem"
    try:
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    except FileNotFoundError:
        pass
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # the web app and the alerts job started together: keep the first
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def public_key():
    """For the phone's pushManager.subscribe (applicationServerKey)."""
    return b64url(_raw_public(vapid_key()))


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload, p256dh, auth):
    """The message body for one phone (RFC 8291, a single aes128gcm record)."""
    ua_public = unb64url(p256dh)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    secret = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(unb64url(auth), secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + body


def _vapid_header(endpoint):
    url = urlparse(endpoint)
    claims = {"aud": f"{url.scheme}://{url.netloc}", "exp": int(time.time()) + 12 * 3600,
              "sub": f"mailto:{settings.PUSH_CONTACT}"}
    token = jwt.encode(claims, vapid_key(), algorithm="ES256")
    return f"vapid t={token}, k={public_key()}"


def send(sub, message):
    """Push one message (a dict) to one phone. Returns the push service's HTTP status."""
    req = urllib.request.Request(sub.endpoint, method="POST", data=encrypt(
        json.dumps(message).encode(), sub.p256dh, sub.auth), headers={
        "Authorization": _vapid_header(sub.endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": "high",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def send_all(subs, message):
    """Push to every phone; forget the ones the push service says are gone. Returns how many got it."""
    sent = 0
    for sub in subs:
        try:
            status = send(sub, message)
        except Exception:
            log.exception("Push to %s failed", urlparse(sub.endpoint).netloc)
            continue
        if status in GONE:
            sub.delete()
        elif 200 <= status < 300:
            sent += 1
        else:
            log.warning("Push to %s refused: HTTP %s", urlparse(sub.endpoint).netloc, status)
    return sent


def call_message(call):
    issue = call.issue + (f" · {call.option}" if call.option else "")
    return {"title": f"Lane {call.lane}: {issue}", "body": call.note or "New lane call",
            "tag": f"call-{call.id}", "url": "/mechanic"}


TEST_MESSAGE = {"title": "MyDay call alerts are on", "body": "This is how a new lane call will look.",
                "tag": "test", "url": "/mechanic"}
