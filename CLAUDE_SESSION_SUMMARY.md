# Claude Code session summary — Hari Hara Ganga distributor_app

A running record of the work done on this app through Claude Code, kept here
so the team (and any future Claude Code session) has a quick reference
without having to dig through PR history. Last updated 2026-10-20.

**32 PRs merged** (branch `claude/wonderful-albattani-o44z9c` → `master`,
auto-merged per the session's standing policy), **80 automated tests**, all
passing.

## Infrastructure: Railway → Oracle Cloud migration + CI/CD

- **#19** Oracle Cloud deployment tooling — `provision.sh`, systemd unit,
  nginx config for the OCI VM (`140.245.198.157`).
- **#20** Cron-based auto-deploy wired up (superseded shortly after).
- **#22** Replaced cron polling with a proper **GitHub Actions deploy
  workflow** — every push to `master` now SSHes in and runs `redeploy.sh`
  automatically, live within seconds.
- **#26** Automated running pending DB migrations on every deploy (closed
  the gap where a new migration needed a manual SSH step — caught this via
  the CreditControlMode rollout).
- **#16** Daily backups now copy to Google Drive, not just local disk.
- HTTPS set up via certbot on `app.hariharaganga.in`; production database +
  uploads migrated from Railway.

## Data integrity & reliability

- **#8** Guardrail against silently double-counting a reconciled Stock
  Issue (the original investigation that kicked off the hardening work —
  invoice HHG/2026-27/0320).
- **#9** Wrapped Sale/Stock-Issue-reconciliation saves in real atomic DB
  transactions (previously multi-step writes could partially fail and
  corrupt the ledger).
- **#11** **Urgent live fix**: a deadlock in `next_invoice_number()` that
  the new test suite caught before it caused a real outage.
- **#10** Added a schema migration tracker (`SchemaMigrations` +
  `run_migrations.py`) — no more guessing which `migrate_*.py` scripts have
  run.
- **#30** Added `Sales.CreatedAt` — every new sale now records its exact
  booking time (prompted by having to reconstruct invoice
  HHG/2026-27/0448's time from nginx logs).
- **#3–#7** Diagnostic tooling for tracing Stock Issue crediting issues
  (IST timestamps, SalePayments visibility, reconciliation-timestamp
  accuracy).

## Security hardening

- **#13** CSRF protection added to all 75 forms across 51 templates.
- **#15** Structured logging + a global unhandled-exception handler.
- **#18** Login rate-limiting (blocks brute-force password guessing).
- **#29** Location-tracking consent gate — a tracked-role user must
  explicitly accept a notice at *every* login (not just a one-time browser
  permission), plus admin-configurable working days.

## Performance

- **#12, #14, #17** Converted every unbounded list page (Sales, Purchases,
  Customers, Suppliers, Employees, Vehicles, Advances) from "load the
  entire table" to server-side search + pagination.

## Business features

- **#1, #2** List/Grid view toggle on record-list pages.
- **#21** "Qty Sold (so far)" now shows on *open* Stock Issues, not just
  reconciled ones.
- **#23** Design notes for a future mixed-flavor combo-case feature
  (parked, not built — see `COMBO_CASE_DESIGN.md`).
- **#24, #25, #27, #28** Customer credit control on New/Edit Sale:
  red-field warning for any customer with dues, evolving into a full
  3-way Admin-configurable mode (Informational / Block after N days /
  Complete block).
- **#31, #32** PDF export for Accounts Receivable's Invoice-wise tab,
  restructured into customer-level + invoice-level sections, alphabetically
  sorted.

## Current live state (as of 2026-10-20)

- Hosted on OCI (`app.hariharaganga.in`), auto-deployed from `master` on
  every merge, migrations run automatically.
- 80 tests across 14 test files guard the core sale/reconciliation/
  credit-control logic.
- Known-good diagnostic pattern for "when/why did invoice X do Y":
  `lookup_invoice.py` (read-only, run on the VM) for Sale details,
  `DeletedSalesLog` for anything that looks like a missing invoice-number
  gap, nginx access logs (`/var/log/nginx/access.log*`) for exact
  timestamps on sales created before `Sales.CreatedAt` existed.
