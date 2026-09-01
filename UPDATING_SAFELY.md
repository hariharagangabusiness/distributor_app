# Updating This App Safely — Without Losing Data

This explains how every update to this app protects the data you've
already entered, and gives you a checklist to follow each time a new
version arrives. Read the "Why this is safe" section once to understand
the guarantee; use the checklist every time you actually update.

---

## Why this is safe by design

Two things make it safe to install a new version of this app without
losing anything:

**1. Your data and the app's code are separate.** Everything you've
entered lives in one file, `db/distributor.db`. The app's code (`app.py`,
the `templates/` folder, etc.) is separate from that file entirely. A new
version of the app is new *code* — it doesn't come with a data file of
its own to overwrite yours with. As long as you don't replace or delete
`db/distributor.db` itself, your data survives any code update
untouched.

**2. Every database change ships as a small, one-way, non-destructive
script.** When a new feature needs a new table or a new column (Leave &
Attendance, attachment-type custom fields, shared leave quotas, and so
on), it comes with a `migrate_*.py` script that:

- **Only adds.** It uses `CREATE TABLE IF NOT EXISTS` for new tables and
  an "add this column if it isn't already there" check for new columns
  on existing tables. It never contains a `DROP TABLE`, `DELETE`, or
  `ALTER TABLE ... DROP COLUMN` against your existing data.
- **Leaves existing rows alone.** Adding a new column to an existing
  table doesn't touch the rows already in it — Products you added last
  year keep every value they had; the new column just starts out empty
  (or a sensible default, like `0`) on those old rows until you fill it
  in, exactly the way an empty cell in a spreadsheet's new column would.
- **Is safe to run more than once.** Every migration script checks what's
  already there before adding anything, so running one twice (or running
  one that turns out not to be needed) does nothing the second time — it
  just prints that there's nothing to do.
- **Is tested before it ships.** Every migration in this project is run
  against a fresh copy of the database — seeded with sample data standing
  in for "data you already entered" — and checked afterward, before any
  update is delivered. That's not a guarantee about *your specific* data,
  which is why step 1 of the checklist below is still "back up first" —
  but it's why these scripts are trustworthy in general.

The net effect: updating the app's code is completely safe on its own
(nothing about it touches your database), and running a `migrate_*.py`
script only ever *adds* — new tables, new columns, new default rows for
new configuration (like new leave types) — never removes or overwrites
what's already in there.

---

## What this does NOT protect against

Being non-destructive by design isn't the same as being indestructible.
The migration scripts protect your data from *the update itself*. They
don't protect against:

- Accidentally deleting `db/distributor.db` yourself, or copying an old
  backup over a newer database by mistake.
- A failing hard drive, a corrupted USB stick, or a computer that's
  stolen or destroyed.
- Someone using the app itself to delete a record they didn't mean to
  (that's normal app usage, not an update problem — nothing in this
  document changes how deleting a record from inside the app works).

That's what **backups** are for (section 8 of the README, and step 1
below) — a second, independent copy of `db/distributor.db`, kept
somewhere the update process (and any of the above) can't reach.

---

## The checklist — follow this every time you update

**1. Back up first, always.** Copy `db/distributor.db` to a USB drive,
cloud storage folder, or anywhere off this computer, before you touch
anything else. This takes a few seconds and is the one step that matters
most — if everything else in this checklist somehow goes wrong, this
copy is how you get back to exactly where you were.

**2. Unzip the new version into a separate, new folder.** Don't extract
the new `distributor_app.zip` on top of your current `distributor_app`
folder. Pick a new location (or a new folder name) so the old one stays
untouched until you're sure the new one works.

**3. Carry your data and secrets across.** From your OLD folder, copy
these into the NEW folder (overwriting the empty placeholders that come
in the zip):
   - `db/distributor.db` — all your data.
   - `uploads/` — every attachment/file anyone has uploaded.
   - `secret_key.txt` — keeps everyone's browser signed in after the
     update instead of being logged out (this file only exists after
     you've run the app at least once — copy it if it's there).

**4. Run every `migrate_*.py` script listed in the new README that you
haven't run for this database yet, in the order listed.** The README's
"Already using this app and just pulled this update?" section (near the
top) always lists the exact commands for that version. Running one you'd
already run before is harmless (see "safe to run more than once" above)
— if you're not sure which you've run, it's fine to run all of them
again.

**5. Start the app from the new folder** (`python app.py`) and check it:
   - Open a record that existed before the update (a product, an
     employee, a past sale) and confirm it looks exactly as it did —
     same values, nothing missing.
   - Look at the new feature the update added, and confirm it starts out
     empty/default rather than with data you didn't expect.
   - If anything looks wrong, stop, don't delete the old folder, and
     restore from your backup (copy the backed-up `db/distributor.db`
     back over the one in the new folder, or just go back to running the
     app from the old folder) while you sort out what happened.

**6. Once you're confident everything's correct, retire the old
folder** (or just leave it — it costs nothing to keep around as an extra
backup for a while).

---

## If a migration script shows an error partway through

Each `migrate_*.py` script is a straight-line sequence of small
add-only steps — if one step fails (for example, a column somehow
already exists in an unexpected shape), it stops there rather than
undoing what it already did, and everything it already added stays
added. Because every step only *adds*, re-running the same script after
fixing whatever caused the error is safe — it picks up wherever it left
off and skips everything it already did (see "safe to run more than
once" above). Your restored backup from step 1 is always there as a
starting-over point if you'd rather not troubleshoot in place.

---

## For anyone who wants to verify this themselves

If you (or someone helping you) wants to check exactly what a migration
script will do before running it, each one is a short, plain-English
Python file — open it in any text editor and read top to bottom; every
one starts with a comment block explaining what it adds and confirming
it doesn't delete anything. You can also inspect the database directly
with a free tool like [DB Browser for SQLite](https://sqlitebrowser.org/)
before and after running a migration, to see for yourself that row counts
in your existing tables haven't changed — only new tables/columns
appeared.
