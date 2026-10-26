# MyDay

The Sun City Tenpin staff app, served at myday.suncitytenpin.com.au.

Staff sign in once with their email and a code (Cloudflare Access), then open the app with the same PIN they use on the login clock. The app has four tabs:

- **MyDay:** today's shift, Log on / Log off and this shift's tasks.
- **My Roster:** upcoming shifts, with a button to confirm each one.
- **RDO:** RDO and leave requests.
- **Messages:** the same staff messages as the login clock, with a "Got it" button.

Under the tabs is a second row of buttons: **Shift Tasks** for everyone, **Mechanic** and **Lane Marshal** for staff switched on for them, and room for more. Techs get a **Mechanic** button: the lane calls from the kiosks, the same list as Lane Marshal's /mechanic page, with On my way and Fixed. The phone beeps and buzzes when a new call comes in while that screen is open.

Log on and Log off ask for the PIN and check the phone's location once. Anyone away from the venue is asked "working off-site today?" and gives a reason. MyDay stores only *on site* or *off site* and that reason. It never stores coordinates or distance.

Built with Django 5.2, HTMX, WhiteNoise and Gunicorn, in one Docker container on VM 106.

## How it fits together

```
phone ──► Cloudflare Access (email + code) ──► tunnel on VM 106 ──► 127.0.0.1:8106 (myday container)
                                                                         │
                                         BowlControl / Vensoft DB on VM 102 (myday_app login)
```

- **Staff, PINs, roster, messages, tasks, leave and the login clock** are all read from BowlControl (`bc/models.py`). Those tables are never migrated by MyDay.
- **MyDay's own data** lives in SQLite in the `myday-data` volume: sessions, PIN attempts, and the on-site/off-site log.
- **Test mode:** with `MYDAY_WRITES_ENABLED=0` (the default), nothing is written to Vensoft. Ticks, confirms and "Got it" last for the session only. Log on and Log off are recorded only in MyDay's own on-site log. Turn writes on once Fletcher has confirmed the tables and values.

## Deploy on VM 106

Prep already done by Seawoz: Docker is installed, `/opt/myday/` exists, and `/opt/myday/.env` holds the `myday_app` password.

```bash
cd /opt/myday
git clone https://github.com/ytrom/myday-suncity-app.git app
cd app

# Merge the template into the existing .env (keep BC_DB_PASSWORD), then:
sudo chmod 600 /opt/myday/.env
#   DJANGO_SECRET_KEY  ->  python3 -c "import secrets; print(secrets.token_urlsafe(50))"
#   MYDAY_DEV_USER_EMAIL -> your staff email, for testing before Cloudflare is set up

docker compose up -d --build
curl -s localhost:8106/healthz            # {"ok": true}
curl -s "localhost:8106/healthz?db=1"     # also checks the BowlControl connection
```

To update later: `git pull && docker compose up -d --build`.

The container listens only on `127.0.0.1:8106`. Fletcher's ANC site keeps Nginx on 80/443 and is not touched.

### Try it before Cloudflare

From your PC, run `ssh -L 8106:localhost:8106 <you>@192.168.0.248`, then open http://localhost:8106 in Chrome. Because `MYDAY_DEV_USER_EMAIL` is set, you're signed in as that staff member and go straight to the PIN pad. This shortcut is ignored for any request that comes through Cloudflare, and also as soon as Cloudflare Access is configured.

### Database permissions for `myday_app`

Run these on VM 102 as the Postgres admin, in the `bowlcontrol` database. Reading only:

```sql
GRANT USAGE ON SCHEMA public TO myday_app;
GRANT SELECT ON accounts_user, accounts_membership,
  core_timeentry, core_bowlnote, core_bowlnoteack,
  roster_shift, roster_shiftassignment, roster_leaverequest,
  tech_techtask, tech_techtask_assignees
TO myday_app;
```

Later, when writes are turned on (after Fletcher confirms):

```sql
GRANT INSERT, UPDATE ON core_timeentry TO myday_app;
GRANT INSERT ON core_bowlnoteack TO myday_app;
GRANT UPDATE (confirmed) ON roster_shiftassignment TO myday_app;
GRANT INSERT ON roster_leaverequest TO myday_app;
GRANT UPDATE (status, completed_at, completed_by_id) ON tech_techtask TO myday_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myday_app;
```

### Mechanic screen

The lane calls live in Lane Marshal's database (`Suncity_Database`, table `tech_control_troublecall`) on the same Postgres server. On VM 102, as the Postgres admin:

```sql
GRANT CONNECT ON DATABASE "Suncity_Database" TO myday_app;
\c Suncity_Database
GRANT USAGE ON SCHEMA public TO myday_app;
GRANT SELECT, UPDATE (attended_at, fixed_at, fixed_by, resolution) ON tech_control_troublecall TO myday_app;
GRANT SELECT ON issue, issue_options TO myday_app;
-- Lane Marshal screen (reporting a lane) also needs:
GRANT INSERT ON tech_control_troublecall TO myday_app;
DO $$ BEGIN EXECUTE format('GRANT USAGE ON SEQUENCE %s TO myday_app', pg_get_serial_sequence('tech_control_troublecall', 'id')); END $$;
```

Then set `MYDAY_MECHANIC=1` in `.env` and rebuild. Staff whose Vensoft role (`accounts_membership.role`) is in `MYDAY_TECH_ROLES` (default `TECH`) get both screens automatically. To give them to anyone else:
`docker compose exec myday python manage.py mechanic someone@example.com on`, and likewise `manage.py marshal` for the Lane Marshal screen. Calls reported from MyDay are logged with ID `APP` (the tablets use `TAB`).

## Cloudflare setup (Morty)

1. **Tunnel.** In Zero Trust, go to Networks, then Tunnels, and create a new tunnel such as `vm106-services`. Install its connector on VM 106 with the command Cloudflare shows. Don't reuse `suncity-live` on VM 100.
2. **Public hostname.** Add `myday.suncitytenpin.com.au` pointing to service `http://localhost:8106`.
3. **Access application.** Under Access, then Applications, add a Self-hosted app for `myday.suncitytenpin.com.au`:
   - Login method: One-time PIN (email code).
   - Policy: Allow, with "Emails" set to the staff email list. Staff need the same email address that is in Vensoft.
   - Session duration: something long, such as 1 month, so staff rarely see the email code. The PIN still guards each opening of the app.
4. **Tell MyDay about it.** Copy the app's *Application Audience (AUD) tag* and your team domain (`<team>.cloudflareaccess.com`) into `/opt/myday/.env`:
   ```
   CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
   CF_ACCESS_AUD=<aud tag>
   MYDAY_DEV_USER_EMAIL=
   ```
   Then run `docker compose up -d`.
5. **Venue Wi-Fi (optional).** Put the venue's public IP in `MYDAY_VENUE_PUBLIC_IPS` so that phones on the venue Wi-Fi count as on site even when location is blocked.

Staff then open the site on their phone and use "Add to Home Screen". It installs like an app.

## Versions

The version shown in the header (and in `/healthz`) is in `myday_project/version.py`. Bump it with every update that goes out: the last part for fixes and small changes (1.0.00 → 1.0.01), the middle for new features (1.0.xx → 1.1.00).
- **1.5.03** (26 Oct 2026): "No fault" button on each call next to Fixed (like Mission Control), for when the tech gets there and nothing is wrong.
- **1.5.02** (25 Oct 2026): Tech Jobs record what the mechanic fixed: issue option = the fix issue, issue_sub_option_id = the matching detail (e.g. Respot / Wire Off Cell). Needs GRANT SELECT ON tech_issuesuboption.
- **1.5.01** (25 Oct 2026): Mechanic Fixed can also log a finished Tech Job in Vensoft (bowlcontrol.tech_techentry) like Lane Marshal: title = the call, description "call · note · fix", issue option matched by name, credited to the tech. Off until MYDAY_TECH_JOBS=1 in .env; needs GRANT SELECT ON tech_issueoption, GRANT INSERT ON tech_techentry and USAGE on its id sequence.

- **1.5.00** (25 Oct 2026): Mechanic Fixed asks what was actually fixed, using Lane Marshal's own fix list and its second-level menus (issue, issue_options), saved to resolution as "Issue — Detail", "Issue" or no_fault. Re-spot pins now go in NOTE as "Pins: 3, 5" like Lane Marshal. Needs GRANT SELECT ON issue, issue_options and GRANT UPDATE (resolution) ON tech_control_troublecall.
- **1.4.00** (25 Oct 2026): once logged on, the shift card goes away and a Finish shift pill sits by the greeting (opens the Log off PIN pad).
- **1.3.02** (25 Oct 2026): rostered shifts can be confirmed any time, past weeks included (never un-confirmed).
- **1.3.01** (25 Oct 2026): confirm each shift's hours (works while still logged on for today), plus Confirm all; Payroll shows "2 of 3" for part-confirmed weeks.
- **1.3.00** (25 Oct 2026): staff confirm their hours each pay week (or flag what's wrong); managers get a Payroll screen showing who has confirmed. Stored in MyDay, not Vensoft.
- **1.2.01** (25 Oct 2026): My Roster shows one pay week (Friday to Thursday) with arrows for earlier and later weeks.
- **1.2.00** (25 Oct 2026): My Roster shows Hours worked for the last 2 weeks from the time log, including days you weren't rostered (marked Extra).
- **1.1.00** (24 Oct 2026): techs get Mechanic and Lane Marshal automatically from their Vensoft role (MYDAY_TECH_ROLES, default TECH); manage.py mechanic/marshal still adds others.
- **1.0.09** (24 Oct 2026): Test mode bar removed from the top (it only shows for the LAN test sign-in now).
- **1.0.08** (24 Oct 2026): optional note for the mechanic on Lane Marshal (saved in NOTE, like the tablet).
- **1.0.07** (24 Oct 2026): Re-spot opens a pin screen (tap the pins); saved as e.g. "Pins 3, 5".
- **1.0.06** (24 Oct 2026): Mechanic and Lane Marshal cover the greeting and both rows of buttons; just the logo bar and the title with Close.
- **1.0.05** (24 Oct 2026): Lane Marshal faults match the live Lane Marshal (13 faults, same wording); button reads Notify mechanic.
- **1.0.04** (24 Oct 2026): lane-call database errors are written to the log; tapping Call the mechanic without a fault says so on the page.
- **1.0.03** (24 Oct 2026): lane report screen: What's wrong? / Lane # / All lanes on one line; fault buttons closer together.
- **1.0.02** (24 Oct 2026): tighter Lane Marshal and Mechanic screens: no help text, "All lanes" beside the lane number.
- **1.0.01** (24 Oct 2026): Mechanic and Lane Marshal keep the top tabs; their title and Close take the place of the second row of buttons.
- **1.0.00** (24 Oct 2026): first numbered release. Second row of buttons (Shift Tasks, Mechanic, Lane Marshal, More).

## Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
DJANGO_DEBUG=1 python manage.py test --settings=tests.settings

# Local preview with demo data (PIN 1234):
mkdir -p /tmp/myday && export SMOKE_DIR=/tmp/myday DJANGO_DEBUG=1 DJANGO_SETTINGS_MODULE=tests.smoke_settings
python manage.py migrate && python -m tests.seed && python manage.py runserver 8106
```

## Still to confirm with Fletcher

- Leave `type` and `status` values (set in `MYDAY_LEAVE_TYPES` and `MYDAY_LEAVE_NEW_STATUS`).
- Tech task status values (`OPEN` / `DONE` assumed).
- How the login clock fills `core_timeentry` (source, notes, payable minutes, locking), before writes are enabled.
- The meaning of `core_bowlnote.action_scope`, `action_status` and `source`.
