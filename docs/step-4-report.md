# MyDay — Step 4 Deploy Report

_Author: Cwas (C.W.A.S) · Date: 23 October 2026_

---

## Snapshot

`before-myday-deploy` taken on VM 106 before any changes.

---

## 4a. Database grants ✅

Grants applied on VM 102 (`bowlcontrol` DB) — all tables found, no errors.

SELECT only on: `accounts_user`, `accounts_membership`, `core_timeentry`, `core_bowlnote`, `core_bowlnoteack`, `roster_shift`, `roster_shiftassignment`, `roster_leaverequest`, `tech_techtask`, `tech_techtask_assignees`

---

## 4b. Code deployment ✅

Cloned `ytrom/myday-suncity-app` on VM 100, rsynced to `/opt/myday/app/` on VM 106. All files present.

---

## 4c. Environment ✅

`/opt/myday/.env` written (mode 600). `MYDAY_WRITES_ENABLED=0` (test mode). `MYDAY_DEV_USER_EMAIL` set to Morty's staff email for LAN testing. CF fields blank — Morty fills after Cloudflare setup. No secrets in this report.

---

## 4d. Build and health checks ✅

| Item | Result |
|---|---|
| Docker version | 29.9.2 |
| Docker Compose version | v5.6.1 |
| Container status | **healthy** (127.0.0.1:8106→8000/tcp) |
| `GET /healthz` | `{"ok": true}` |
| `GET /healthz?db=1` | `{"ok": true, "bowlcontrol": "ok"}` |

### Model counts (no personal data)

| Model | Count |
|---|---|
| User | 33 |
| Membership | 38 |
| BowlNote | 39 |
| Shift | 3,851 |
| TechTask | 4,816 |
| LeaveRequest | 170 |
| Staff with PIN set | 33 |

All counts match Step 3 DB recon ✅

---

## 4e. SSH tunnel test

```bash
ssh -L 8106:localhost:8106 suncityas@192.168.0.248
```

Open **http://localhost:8106** in Chrome. Yellow "Test mode" bar expected. PIN test pending — Morty to confirm.

---

## Outstanding before Cloudflare

- [ ] Morty confirms PIN unlock works via SSH tunnel
- [ ] Cloudflare tunnel on VM 106 → `http://localhost:8106`
- [ ] Cloudflare Access app (email codes)
- [ ] Paste `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` into `/opt/myday/.env`
- [ ] Clear `MYDAY_DEV_USER_EMAIL` once Cloudflare is live
- [ ] Enable writes (`MYDAY_WRITES_ENABLED=1`) once Fletcher confirms tables/values
- [ ] Step 1b: password rotation (Morty timing)

---

## Notes

- Container on `127.0.0.1:8106` only — ANC Nginx untouched ✅
- Volume `myday-data` holds sessions/log — **never `docker compose down -v`**
- Restart: `docker compose -f /opt/myday/app/docker-compose.yml restart`
- Update code: rsync from VM 100, then `docker compose up -d --build`

---
_Cwas — 23 October 2026. No secrets in this document._
