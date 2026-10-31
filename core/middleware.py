"""Who is using MyDay (Cloudflare Access email) and whether they've unlocked it with their PIN."""
import logging
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlparse

import jwt
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone

from . import services

log = logging.getLogger(__name__)

OPEN_PATHS = ("/static/", "/healthz", "/manifest.webmanifest", "/sw.js")


@lru_cache(maxsize=1)
def _jwks_client():
    return jwt.PyJWKClient(f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True)


def cloudflare_email(request):
    """The signed-in email from Cloudflare Access, verified against Cloudflare's keys."""
    token = request.META.get("HTTP_CF_ACCESS_JWT_ASSERTION") or request.COOKIES.get("CF_Authorization")
    if not token:
        return None
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=settings.CF_ACCESS_AUD,
            issuer=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        )
    except Exception as exc:  # bad, expired or forged token
        log.warning("Cloudflare Access token rejected: %s", exc.__class__.__name__)
        return None
    return claims.get("email")


def resolve_email(request):
    """Returns (email, test_mode)."""
    if settings.CF_ACCESS_TEAM_DOMAIN and settings.CF_ACCESS_AUD:
        return cloudflare_email(request), False
    # Before Cloudflare is configured: allow a fixed test user, but only for requests that
    # did NOT come through Cloudflare, so a half-configured tunnel can't use it.
    if settings.DEV_USER_EMAIL and "HTTP_CF_RAY" not in request.META:
        return settings.DEV_USER_EMAIL, True
    return None, False


class CloudflareIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.staff = None
        request.membership = None
        request.identity_test_mode = False
        if request.path.startswith(OPEN_PATHS):
            return self.get_response(request)

        email, test_mode = resolve_email(request)
        if not email:
            return render(request, "core/denied.html", {"reason": "signin"}, status=403)
        staff = services.staff_for_email(email)
        if not staff:
            return render(request, "core/denied.html", {"reason": "not_staff", "email": email}, status=403)

        request.staff = staff
        request.membership = services.membership_for(staff)
        request.identity_test_mode = test_mode
        return self.get_response(request)


def _redirect(request, url):
    if request.headers.get("HX-Request"):
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = url
        return resp
    return HttpResponseRedirect(url)


class PinLockMiddleware:
    """The app opens on the PIN pad, and locks again after MYDAY_PIN_IDLE_MINUTES of no use."""

    UNLOCK_PATH = "/unlock"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(OPEN_PATHS) or request.staff is None or request.path == self.UNLOCK_PATH:
            return self.get_response(request)

        s = request.session
        until = s.get("unlocked_until")
        now = timezone.now().timestamp()
        if s.get("unlocked_user") != request.staff.id or not until or until < now:
            s.pop("unlocked_until", None)
            s["after_unlock"] = _return_path(request)
            return _redirect(request, self.UNLOCK_PATH)

        s["unlocked_until"] = now + timedelta(minutes=idle_minutes(request.staff)).total_seconds()
        return self.get_response(request)


def idle_minutes(staff):
    """How long MyDay stays unlocked with no use. Mechanics and marshals get a whole shift
    (MYDAY_TOOL_PIN_IDLE_MINUTES), so a call can be logged or fixed without the PIN again."""
    if any(services.lane_tools(staff).values()):
        return max(settings.PIN_IDLE_MINUTES, settings.TOOL_PIN_IDLE_MINUTES)
    return settings.PIN_IDLE_MINUTES


def _return_path(request):
    """Where to go after the PIN: the page that was asked for. For a form or an HTMX poll,
    the page it came from (the form itself can't be replayed after the PIN)."""
    if request.method == "GET" and not request.headers.get("HX-Request"):
        return request.get_full_path()
    referer = urlparse(request.headers.get("Referer", ""))
    if referer.netloc == request.get_host() and referer.path:
        return referer.path + (f"?{referer.query}" if referer.query else "")
    return None


def after_unlock_path(request):
    """The saved page to go back to after the PIN, if it is one of ours."""
    path = request.session.pop("after_unlock", None) or ""
    if path.startswith("/") and not path.startswith("//") and not path.startswith(PinLockMiddleware.UNLOCK_PATH):
        return path
    return None


def unlock_session(request):
    after = request.session.get("after_unlock")
    request.session.cycle_key()
    request.session["after_unlock"] = after
    request.session["unlocked_user"] = request.staff.id
    request.session["unlocked_until"] = (
        timezone.now() + timedelta(minutes=idle_minutes(request.staff))
    ).timestamp()
