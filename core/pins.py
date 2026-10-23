"""Checking login-clock PINs against accounts_membership.pin_hash.

Vensoft stores PINs two ways:
- Django's standard hashes (pbkdf2_sha256$...), checked with check_password.
- Its own quick "fast" format, which Fletcher says is SHA-256. We don't have that code, so
  match_fast tries the usual ways of salting a SHA-256 and reports which one matched
  (see manage.py pin_check). Once we know, MYDAY_FAST_PIN_SCHEME pins it to that one.
"""
import base64
import hashlib
import hmac
import os

from django.contrib.auth.hashers import check_password


def _encodings(raw):
    b64 = base64.b64encode(raw).decode()
    url = base64.urlsafe_b64encode(raw).decode()
    return {"hex": raw.hex(), "b64": b64, "b64nopad": b64.rstrip("="), "url": url, "urlnopad": url.rstrip("=")}


def _schemes(pin, salt, pepper):
    """Every candidate SHA-256 of the PIN, by name."""
    p, s = pin.encode(), (salt or "").encode()
    out = {"sha256(pin)": hashlib.sha256(p).digest()}
    if salt:
        out.update({
            "sha256(salt+pin)": hashlib.sha256(s + p).digest(),
            "sha256(pin+salt)": hashlib.sha256(p + s).digest(),
            "sha256(salt$pin)": hashlib.sha256(s + b"$" + p).digest(),
            "sha256(salt:pin)": hashlib.sha256(s + b":" + p).digest(),
            "sha256(pin:salt)": hashlib.sha256(p + b":" + s).digest(),
            "hmac(salt,pin)": hmac.new(s, p, hashlib.sha256).digest(),
            "hmac(pin,salt)": hmac.new(p, s, hashlib.sha256).digest(),
        })
    if pepper:
        k = pepper.encode()
        out.update({
            "hmac(pepper,pin)": hmac.new(k, p, hashlib.sha256).digest(),
            "sha256(pepper+pin)": hashlib.sha256(k + p).digest(),
            "sha256(pin+pepper)": hashlib.sha256(p + k).digest(),
        })
        if salt:
            out.update({
                "hmac(pepper,salt+pin)": hmac.new(k, s + p, hashlib.sha256).digest(),
                "hmac(pepper,salt$pin)": hmac.new(k, s + b"$" + p, hashlib.sha256).digest(),
                "hmac(pepper,pin+salt)": hmac.new(k, p + s, hashlib.sha256).digest(),
                "sha256(pepper+salt+pin)": hashlib.sha256(k + s + p).digest(),
            })
    return out


def match_fast(pin, encoded):
    """Name of the scheme that reproduces a fast$... hash for this PIN, or None."""
    parts = encoded.split("$")
    if parts[0] != "fast" or len(parts) < 2:
        return None
    digest, salt = parts[-1], "$".join(parts[1:-1])
    pepper = os.environ.get("MYDAY_PIN_PEPPER", "")
    only = os.environ.get("MYDAY_FAST_PIN_SCHEME", "")
    for name, raw in _schemes(pin, salt, pepper).items():
        if only and name != only.split("/")[0]:
            continue
        for enc, text in _encodings(raw).items():
            if only and "/" in only and enc != only.split("/")[1]:
                continue
            if hmac.compare_digest(text, digest):
                return f"{name}/{enc}"
    return None


def describe(encoded):
    """Shape of a stored hash without revealing it: format, then the length and kind of each part."""
    if not encoded:
        return "EMPTY"
    parts = encoded.split("$")

    def kind(x):
        if all(c in "0123456789abcdef" for c in x):
            return "hex"
        if all(c.isalnum() or c in "+/=-_" for c in x):
            return "b64"
        return "text"

    return parts[0] + " " + " ".join(f"[{len(x)} {kind(x)}]" for x in parts[1:])


def pin_matches(pin, encoded):
    if not encoded:
        return False
    if encoded.startswith("fast$"):
        return match_fast(pin, encoded) is not None
    try:
        return check_password(pin, encoded)
    except ValueError:  # a hasher whose library isn't installed
        return False
