# BillSoft Backend

Django + DRF + PostgreSQL API for the BillSoft multi-tenant billing platform. This README covers everything needed to go from a clean machine to a running API server, plus the gotchas the team already hit once — worth reading before repeating them.

For the frontend counterpart, see the `billsoft-web` repo's own README and architecture handoff doc.

---

## 1. Prerequisites

This section is split by OS — follow the one for your machine.

### Python

**macOS — use Homebrew's Python, not Xcode's bundled one**

macOS ships a Python via Xcode's command-line tools (often an outdated 3.9) — **do not use it.** This project targets **Python 3.12**.

```bash
brew install python@3.12
python3.12 --version   # confirm 3.12.x
which python3.12        # confirm the path is under /opt/homebrew, NOT /Applications/Xcode.app
```

**Windows**

Install Python 3.12 from [python.org](https://www.python.org/downloads/) (the official installer) or via `winget`:

```powershell
winget install --id Python.Python.3.12
```

**On the installer's first screen, check "Add python.exe to PATH"** — easy to miss, and everything downstream assumes `python` is on PATH.

```powershell
python --version   # confirm 3.12.x
where python        # confirm the path points at your real install, not a stub
```

If `python --version` opens the Microsoft Store instead of printing a version, that's Windows' "app execution alias" intercepting the command — go to **Settings → Apps → Advanced app settings → App execution aliases** and turn off the `python.exe`/`python3.exe` entries, then re-open your terminal.

### PostgreSQL

**macOS**

```bash
brew install postgresql@16
```

Start it — if `brew services start postgresql@16` errors (a known Homebrew internal bug on some setups), run `brew update && brew upgrade` first, then retry. If it still fails, start Postgres manually instead:

```bash
brew --prefix postgresql@16   # note the path it prints
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 start
```

Confirm it's running:
```bash
psql postgres   # should drop you into a postgres=# prompt; \q to exit
```

**Windows**

Install PostgreSQL 16 from the [official installer](https://www.postgresql.org/download/windows/) (EDB's installer), or via `winget`:

```powershell
winget install --id PostgreSQL.PostgreSQL.16
```

The installer sets `postgres` as a Windows **service**, so it starts automatically — no manual `start` step needed. It will also prompt you to set a password for the `postgres` superuser during install; remember it, you'll need it below.

Add Postgres's `bin` folder to PATH if the installer didn't already (typically `C:\Program Files\PostgreSQL\16\bin`), then confirm it's running:

```powershell
psql -U postgres   # enter the password you set; should drop you into a postgres=# prompt; \q to exit
```

If the service isn't running, check **Services** (`services.msc`) for `postgresql-x64-16` and start it there.

### A Postgres GUI (optional, but recommended)

**macOS**

```bash
brew install --cask tableplus        # free tier, clean UI
# or, fully free with no limits:
brew install --cask dbeaver-community
```

**Windows**

```powershell
winget install --id TablePlus.TablePlus        # free tier, clean UI
# or, fully free with no limits:
winget install --id dbeaver.dbeaver
```

pgAdmin also ships bundled with the Windows Postgres installer if you'd rather not install anything extra.

Connect with: Host `127.0.0.1`, Port `5432`, Database `billsoft_dev`. **macOS:** User = your macOS username (not `postgres`, not the database name — this trips people up), password blank. **Windows:** User = `postgres`, password = whatever you set during install.

---

## 2. Getting the project

```bash
git clone https://github.com/SupremeBillSoft/billsoft-backend.git
cd billsoft-backend
```

Same command on Windows — install Git first if needed (`winget install --id Git.Git`), and run this in PowerShell or Git Bash.

## 3. Virtual environment

**macOS**

```bash
python3.12 -m venv venv
source venv/bin/activate
```

**You must run `source venv/bin/activate` every time you open a new terminal** to work on this project — it does not persist across terminal sessions. If `python`/`manage.py` commands say "command not found," this is almost always why; check your prompt shows `(venv)` before troubleshooting anything else.

**Windows**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

(Using `cmd.exe` instead of PowerShell? Run `venv\Scripts\activate.bat` instead.)

**Same rule applies — activate the venv every new terminal session**, and check for `(venv)` in your prompt before troubleshooting "command not found" errors.

If PowerShell blocks the activation script with a "running scripts is disabled on this system" error, that's its execution policy — run this once per terminal session, then activate again:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

## 4. Install dependencies

```bash
pip install -r requirements.txt
```

## 5. Create the database

**macOS**

```bash
createdb billsoft_dev
psql -l | grep billsoft_dev   # confirm it exists
```

**Windows**

```powershell
createdb -U postgres billsoft_dev
psql -U postgres -l   # confirm billsoft_dev is in the list
```

(`createdb`/`psql` need Postgres's `bin` folder on PATH — see the PostgreSQL install step above if these aren't recognized.)

## 6. Environment variables

**macOS**

```bash
cat > .env << 'EOF'
DEBUG=True
SECRET_KEY=dev-only-change-in-production
DATABASE_URL=postgres://localhost:5432/billsoft_dev
EOF
```

**Windows**

The bash heredoc above doesn't work in PowerShell or `cmd`. Use one of these instead:

```powershell
@"
DEBUG=True
SECRET_KEY=dev-only-change-in-production
DATABASE_URL=postgres://localhost:5432/billsoft_dev
"@ | Out-File -Encoding utf8 .env
```

Or just create a `.env` file by hand in your editor with the same three lines. Either way, **double-check the file has no trailing `.txt` extension** (Windows Explorer hides known extensions by default, so `.env` can silently become `.env.txt`) — `ls`/`dir` in the terminal will show the real name.

## 7. Run migrations

```bash
python manage.py migrate
```

Expect ~18+ migrations to apply on a genuinely fresh database (Django's own `admin`/`auth`/`contenttypes`/`sessions` apps, plus `tenants`, `items`, `parties`). If it says "No migrations to apply" on a brand-new database, something's wrong — see the gotchas section below.

### Upgrading a dev database created before the Purchase BRD / Sales release

`purchase/migrations/0001_initial.py` was regenerated for the BRD-aligned purchase models (PR, PO, Purchase Invoice, Return, Debit Note). If your database already applied the **old** `purchase.0001_initial`, the new purchase migrations will fail. Purchase data from that old schema can't be carried over, so reset it once:

```bash
# Simplest: recreate the whole database (loses all local data)
dropdb billsoft_dev && createdb billsoft_dev && python manage.py migrate

# Or keep masters (parties, items, ...) and only reset purchase:
psql billsoft_dev -c "DROP TABLE IF EXISTS purchase_order_term, purchase_order_item, purchase_order, purchase_requisition_item, purchase_requisition CASCADE;"
psql billsoft_dev -c "DELETE FROM django_migrations WHERE app = 'purchase';"
python manage.py migrate
```

A fresh database needs none of this.

## 8. Create an admin user

```bash
python manage.py createsuperuser
```

This is a **Django-only** admin account for `/admin/` — the backend's internal data-browsing panel. It's unrelated to Keycloak or any tenant's actual users; nobody outside the dev team should have these credentials.

## 9. Run the server

```bash
python manage.py runserver
```

Visit `http://127.0.0.1:8000/admin/` and log in with the superuser to confirm everything's connected.

---

## 10. Verify with the real endpoints

```bash
curl http://127.0.0.1:8000/api/parties/
curl http://127.0.0.1:8000/api/items/
```

Both should return `[]` on a fresh database, not an error.

---

## 11. Linting

```bash
pip install ruff   # if not already in requirements.txt
ruff check .
```

`pyproject.toml` has `RUF012` (mutable class default) disabled specifically for `models.py` files — that rule is a false positive against Django's own idiomatic `Meta.constraints`/`indexes` patterns; every other file keeps the real protection.

---

## 12. Project structure

```
config/          — settings.py, urls.py, wsgi.py
tenants/         — Tenant model, DocNumberSequence (shared document-numbering engine), get_current_tenant()
items/           — Item Master: models, serializers, viewsets, bulk-import
parties/         — Party Master: models, serializers, viewsets, bulk-import
```

**`tenants/services.py`'s `get_current_tenant()`** is the one function every viewset calls to scope its queryset — right now it returns a hardcoded dev tenant (`get_or_create`d automatically), since real Keycloak/JWT auth isn't wired in yet. This is the **single place** that changes once auth is real; nothing else needs to.

**`DocNumberSequence`** is the shared, atomic (`select_for_update`) document-numbering engine — built generically so Party Code, Purchase Requisition numbers, and every future numbered document (PO, Invoice, etc.) all use the same mechanism, keyed by `(tenant, doc_type)`.

---

## 13. What's real vs. what's still stubbed — read this before assuming something works

| Piece | Status |
|---|---|
| Party & Item CRUD, nested writes, tenant scoping | ✅ Real, tested |
| Party Code / doc-number atomic suffix generation | ✅ Real |
| CORS (frontend ↔ backend on localhost) | ✅ Configured — `CORS_ALLOWED_ORIGINS` in `settings.py`, currently only `localhost:5173`/`127.0.0.1:5173`. **Add your real subdomains before deploying** — never switch to `CORS_ALLOW_ALL_ORIGINS` |
| camelCase JSON (matches frontend field naming) | ✅ Via `djangorestframework-camel-case` |
| Bulk Excel import (Party & Item) | ✅ Endpoints exist (`POST /api/parties/bulk-import/`, `/api/items/bulk-import/`) — see the ADR doc for the ingestion design |
| **Authentication** | ❌ None. Every endpoint is currently open, scoped only to the one hardcoded dev tenant. **Do not expose this server outside localhost as-is.** |
| **Login-dependent placeholders** — revisit when registration + login land | ⚠️ `tenants/services.py`: `get_current_tenant()` (hardcoded tenant) and `current_user_name()` (feeds Created/Updated By on stock adjustments, movements and transfers — currently "Unknown (login pending)"). `current_user_role()` returns no role, so role-based PR/PO auto-approval (Company Settings) can't fire yet. Approval decisions take **Approved by** as typed input (`approvals/serializers.py`) and `approvals/mixins.py` `_can_approve()` lets everyone approve. All four should read the logged-in user / role once login exists. |
| Company Settings (state, GSTIN, auto-approve PR/PO) | ✅ `GET/PATCH /api/company-settings/` — the company State decides CGST+SGST vs IGST on every purchase line |
| Inventory (godowns, stock ledger, adjustments, transfers) | ✅ `inventory` app — stock is a ledger of `StockMovement` rows, never a counter; Purchase Invoice posts in, Purchase Return posts out, nothing may go below zero. Item opening stock (BRD 6.3.2) isn't built yet — use an Add adjustment. |
| Keycloak | ❌ Not wired in — see `get_current_tenant()`'s TODO |
| Row-Level Security (RLS) | ❌ Not yet enabled at the Postgres level — tenant isolation currently relies entirely on `get_current_tenant()`'s queryset filtering in application code, not a database-enforced guarantee yet |
| Redis / Celery | ❌ Not deployed — bulk-import runs synchronously in the request for now |
| MinIO / file storage | ⚠️ Flagged for reconsideration — MinIO's licensing changes put it in maintenance-only mode for our needs; alternatives (SeaweedFS, Garage, or a cloud object storage service) are still being evaluated, not decided |
| Postgres backups | ❌ Not configured — this is a real, standing risk on the current dev database |

---

## 14. Common gotchas the team already hit — save yourself the round-trip

- **Windows: `OperationalError` mentioning a socket path like `/localhost:5432/.s.PGSQL.5432`.** This means psycopg is trying to connect over a Unix socket instead of TCP — it happens when `HOST` and `PORT` get combined into one string (e.g. `DATABASE_URL=postgres://localhost:5432/...` parsed incorrectly, or a settings.py that concatenates `host:port` into a single `HOST` value) instead of being passed as separate settings. Fix: make sure `HOST` and `PORT` reach `DATABASES['default']` as two distinct values, not one combined string.
- **`INSTALLED_APPS`/`MIDDLEWARE`/`TEMPLATES` must be the FULL Django default lists**, not partially replaced. Removing the built-in `django.contrib.*` entries or dropping `TEMPLATES`/`MIDDLEWARE` causes cryptic `admin.E403`/migration errors that don't obviously point back to the missing config.
- **Only one `DATABASES` block per file.** If you ever see two (e.g., an old SQLite block left in from scaffolding, plus a new Postgres one), Python silently uses whichever is defined *last* — no error, just quietly wrong behavior. Delete the old one entirely.
- **`Meta.constraints`/`indexes` must be a real list, not an accidental one-element tuple.** `(x,)` is a tuple; `(x)` is just `x` — a very easy typo that produces a confusing "not iterable" error at migration time, not at the point of the typo.
- **A route/URL param name and its `useParams()` key must match exactly on the frontend** — not this repo's bug, but if the frontend team reports "edit form shows blank," check that first before assuming it's an API issue.

---

## 15. Pushing to GitHub

```bash
git add .
git commit -m "type: description"   # feat / fix / refactor / docs / chore
git push origin <branch-name>
```

Same branching strategy as the frontend repo — feature branches, PR review required, no direct pushes to `main`. See `CONTRIBUTING.md` if this repo has one; if not, it's worth copying the frontend repo's over and adapting it, rather than writing a second version from scratch.
