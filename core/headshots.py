"""Athlete head shots (Morty, 20 Nov 2026): Event Tools > Athlete Head Shot.

Staff pick a ComScore event, find the bowler, snap a photo and upload it. Mission Control keeps the photos
(MissionControl/headshots) and Banner Builder V2 puts the face in the bowler's circle on the LED wall banners.
MyDay talks to Mission Control's /api/headshots on the venue network (the phone never reaches it directly).
ComScore is only read, by Mission Control. Nothing here writes to ComScore or Vensoft.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

TIMEOUT = 10
CODE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
PHOTO_ID = re.compile(r"^[A-Z0-9-]{1,80}$")
MAX_BYTES = 3 * 1024 * 1024


class McError(Exception):
    """Mission Control couldn't be reached, or said no. The message is safe to show staff."""


def _url(query):
    return f"{settings.MC_URL.rstrip('/')}/api/headshots?{query}"


def _call(url, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error")
        except Exception:
            msg = None
        raise McError(msg or f"Mission Control said no ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise McError("Can't reach Mission Control right now. Try again in a moment.") from exc


def _json(url, data=None, method=None):
    body, _ = _call(url, data, method)
    return json.loads(body)


def events():
    return _json(_url("events"))["events"]


def event(code):
    if not CODE.match(code):
        raise McError("That isn't an event code.")
    return _json(_url("event=" + urllib.parse.quote(code)))


def photo(photo_id):
    if not PHOTO_ID.match(photo_id):
        raise McError("Bad photo")
    return _call(_url("photo=" + photo_id))[0]


def upload(code, no, jpeg, by):
    if not CODE.match(code):
        raise McError("That isn't an event code.")
    if len(jpeg) > MAX_BYTES:
        raise McError("That photo is too big.")
    if jpeg[:2] != b"\xff\xd8":
        raise McError("That isn't a photo MyDay can send. Take it again.")
    payload = {"code": code, "no": no, "by": by, "image": base64.b64encode(jpeg).decode()}
    return _json(_url(""), json.dumps(payload).encode(), "POST")


def remove(photo_id):
    if not PHOTO_ID.match(photo_id):
        raise McError("Bad photo")
    return _json(_url("id=" + photo_id), method="DELETE")


def local_url(photo):
    """The MyDay address of a bowler's head shot (MyDay passes it through from Mission Control)."""
    if not photo:
        return ""
    return f"/events/headshots/photo/{photo['id']}?v={urllib.parse.quote(photo.get('at', ''))}"
