# Deploying Hari Hara Ganga to Railway

This app now ships ready for Railway: a `Procfile` runs it under gunicorn
(a real production server, not Flask's dev server), the port is read from
Railway's `$PORT`, and the database path and session secret can both be
pointed at Railway's persistent storage via environment variables. Follow
these steps in order the first time; after that, every `git push` to your
connected branch redeploys automatically.

## 1. Push the code to GitHub

From inside the `distributor_app` folder:

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

A `.gitignore` is already included so the local database file, the
generated `secret_key.txt`, and any `uploads/` files are never committed —
those hold real business data and shouldn't end up in a public or shared
repo.

If you already have a repo, just copy this folder's contents into it
(replacing the earlier files) and commit/push as usual.

## 2. Create the Railway project

1. Go to [railway.app](https://railway.app) and sign in (GitHub login is
   easiest since you'll be connecting a GitHub repo anyway).
2. **New Project → Deploy from GitHub repo** → pick the repo you just
   pushed. If Railway asks, install/authorize the Railway GitHub App for
   that repo.
3. Railway will detect the `Procfile` and `requirements.txt` and start a
   build automatically. Let this first build run — it will likely **crash
   on start**, which is expected, because the two steps below haven't
   happened yet.

## 3. Attach a Volume (critical — do this before adding real data)

The app stores everything — every product, sale, stock issue, employee,
salary record — in a single SQLite file. Railway's regular filesystem is
wiped on every redeploy and restart, so without a Volume **all your data
would disappear the next time you deploy a change**.

1. In your Railway project, open the service → **Settings → Volumes** (or
   the **Volumes** tab, depending on Railway's current UI) → **New Volume**.
2. Set the **Mount Path** to `/data`.
3. Add an environment variable (next section) so the app actually writes
   there instead of its default local folder.

## 4. Set environment variables

Service → **Variables** → add:

| Variable | Value | Why |
|---|---|---|
| `DATA_DIR` | `/data` | Points the SQLite database at the mounted Volume from step 3, so it survives redeploys. |
| `SECRET_KEY` | a long random string, e.g. output of `python -c "import secrets; print(secrets.token_hex(32))"` | Signs login sessions. Without this, the app falls back to writing a key file to local disk, which — like the database — would reset on every redeploy and silently log everyone out. |

Leave `PORT` alone — Railway sets it automatically and the app already
reads it.

Do **not** set `FLASK_DEBUG` — leaving it unset keeps Flask's debug mode
off, which matters for security on a public deployment (Flask's debugger
allows remote code execution from any page that hits an unhandled error).

## 5. Initialize the database

The app creates all its tables automatically on startup (`db.init_db()`
runs on import, safe to run every time — it never touches existing data).
So once the Volume and `DATA_DIR` are set, a redeploy is enough to get an
empty, ready database — no separate migration step needed for a brand new
deployment.

What you do need to do once, after the first successful deploy: create
your first Admin login, since a fresh database has no users yet. Easiest
way — open a shell against the running service:

1. Service → the **⋮** menu (or **Settings**) → **Shell** (Railway calls
   this a one-off command / shell into the running container — the exact
   label has moved around in their UI, look for "Shell" or "Run a command").
2. Run:
   ```
   python migrate_auth.py
   ```
   This is the same script used locally — it prompts for a username and
   password and creates the first Admin account. If Railway's shell doesn't
   give you an interactive prompt, set `ADMIN_USERNAME` and
   `ADMIN_PASSWORD` as environment variables first (temporarily, in the
   Variables tab, or inline for that one command) and the script will use
   those instead of prompting — the script already supports this.

If Railway's shell access isn't available on your plan, the alternative is
running the same command locally against a copy of the Volume's database,
or temporarily adding a one-off startup script — ask if you hit this and
we can work around it.

## 6. Redeploy

Trigger a redeploy (Railway usually does this automatically once you save
variables and the Volume — otherwise use **Deploy** in the dashboard).
Once it's up, Railway gives you a public URL under **Settings →
Networking** (something like `your-app.up.railway.app`, or attach your own
custom domain there). Open it, go to `/login`, and sign in with the Admin
account you just created.

## Notes on future updates

- Every phase zip we've built so far (logo, targets, incentives, color
  coding, the cash/bank split, etc.) applies the same way: replace the
  listed files in your local copy of the repo, `git commit`, `git push`.
  Railway redeploys automatically. Since the database lives on the Volume
  (not in the repo), your data is untouched by this.
- Any phase that ships a `migrate_*.py` script needs that script run once
  after deploying, the same way as step 5 above (Shell → `python
  migrate_whatever.py`), before using the new feature.
- The Procfile runs a single gunicorn worker (`--workers 1`) on purpose —
  SQLite doesn't handle many processes writing to the same file well, and
  this app is sized for a small distributor team, not high concurrency. If
  you ever outgrow that, the real fix is switching the database engine
  (e.g. to Postgres, which Railway can also host), not just adding more
  gunicorn workers on top of SQLite.
