# Daily backup setup (one-time, ~1 minute)

This creates a Windows scheduled task that runs `backup_daily.ps1` every night
at 2:00 AM, entirely on your laptop — it does not depend on Claude, the
desktop app being open, or an internet connection to anything except Railway
itself (which the script reaches the same way `railway.exe ssh` already does
when you run it by hand).

## What it backs up, every day, into `C:\distributor_app\backups\<YYYY-MM-DD>\`

- `production_distributor.db` — a full copy of the live Railway database
- `laptop_distributor.db` — a copy of your laptop's local database (if you use one)
- `app_code.zip` — a snapshot of app.py, all other .py files, and the templates/static folders
- `backup_log.txt` — what happened during that day's run (useful if something fails silently)

Backups older than 60 days are automatically deleted so the folder doesn't grow forever.

## One-time setup

1. Open **PowerShell as Administrator** (right-click Start → "Windows PowerShell (Admin)" or "Terminal (Admin)").
2. Run this single command:

```powershell
schtasks /Create /TN "DistributorApp Daily Backup" /TR "powershell.exe -ExecutionPolicy Bypass -File C:\distributor_app\backup_daily.ps1" /SC DAILY /ST 02:00 /RU "%USERNAME%" /F
```

That's it — Windows will now run the backup every night at 2:00 AM, whether or not you're logged in (as long as the laptop is on).

## Testing it right away

You don't have to wait until 2 AM. Run it manually any time to check it works:

```powershell
powershell -ExecutionPolicy Bypass -File C:\distributor_app\backup_daily.ps1
```

Then check `C:\distributor_app\backups\<today's date>\backup_log.txt` for a line-by-line
report of what succeeded.

## If the production DB backup fails

The most likely cause is that `railway.exe` isn't linked/logged in under the
Windows user account the scheduled task runs as. Run `.\railway.exe whoami`
in a normal PowerShell window first — if that's not logged in, run
`.\railway.exe login` once, and the scheduled task (running as the same
Windows user) should then work too.

## Restoring from a backup (if you ever need to)

- **Production**: run `.\railway.exe ssh` to get a shell in the live
  container, then use Python to write the saved `.db` file back into
  `db/distributor.db` (ask Claude to walk you through this if/when it's
  actually needed — it's a careful, one-time operation, not something to
  practice on a live database casually).
- **Laptop**: just copy `laptop_distributor.db` from the dated backup folder
  back to `C:\distributor_app\db\distributor.db` (with the app stopped first).
