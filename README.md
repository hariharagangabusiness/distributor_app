# Distributor Operations Management App

A ready-to-run app for managing a distributor's day-to-day operations:
inventory with min/max stock alerts, sales with GST-compliant invoice
generation, purchases, operating expenses, transportation vehicle
maintenance schedules, and employee salary + advance payments on a
monthly schedule — all in one browser-based dashboard. Employee leave &
attendance tracking (with automatic loss-of-pay salary calculation), file
attachments, admin-defined custom fields (including attachment-type
fields), a customizable dashboard, and customizable list columns are all
built in too — see sections 5 and 6 below. The app now also requires
signing in (individual accounts per staff member) and has a light/dark
mode toggle — see section 3. A GST Filing tab computes monthly GSTR-1 and
GSTR-3B reference reports from your own records, reconciles them against
an uploaded GSTR-2B, and sends due-date reminders (in-app and, optionally,
by email) — see section 7b.

This works on any computer (Windows, Mac, Linux) with Python installed,
and a few staff on the same office network can use it at once from their
own browsers, no installation needed on their machines.

A separate, native **MS Access** version of the same system is provided
in the `access_package` folder for anyone who specifically wants an
Access-based (.accdb) database — see that folder's `Instructions.md`.
That version requires MS Access on Windows and a manual one-time build
step (Access can't be built or tested in the cloud). This web app is the
one you can run immediately.

---

## Requirements

- Python 3.9 or newer ([python.org](https://www.python.org/downloads/))
- No internet connection required to run it (only the page styling loads
  a couple of small files from a CDN the first time; the app itself works
  fully offline once those are cached by the browser)

---

## 1. First-time setup

Open a terminal / command prompt in this folder (`distributor_app`) and run:

```
pip install -r requirements.txt
python init_db.py
python migrate_auth.py
```

The first creates an empty database at `db/distributor.db`. The second
walks you through creating your first login (an Admin account — you'll
choose the username and password) since the app now requires signing in.
Once that's done you're ready to start entering your own products,
suppliers, customers, vehicles, and employees.

**Want to see it working with sample data first?** Instead of
`init_db.py` + `migrate_auth.py`, run:

```
python seed_demo.py
```

This fills in realistic demo data (products with HSN/GST rates, a
Maharashtra-registered company profile, a few purchases/sales — including
both a same-state and an inter-state invoice — vehicles with maintenance
due, employees with a sample advance) so you can explore every screen,
including a generated GST invoice, immediately — and it prints a ready-made
demo login (`demo` / `demo1234`) so you can sign in right away. Delete
`db/distributor.db` and re-run `init_db.py` + `migrate_auth.py` whenever
you want to start clean with your own data and your own login.

**Already using this app and just pulled this update?** Don't re-run
`init_db.py` — that only adds missing tables and won't touch your data.
Run whichever of these you haven't run yet (all are non-destructive —
they only add new tables/columns, never delete anything — and safe to
run again if you're not sure):

```
python migrate_gst_invoice.py
python migrate_customization.py
python migrate_leave_attendance.py
python migrate_auth.py
python migrate_shared_leave_quota.py
python migrate_gst_filing.py
python migrate_purchase_import.py
python migrate_stock_issues.py
python migrate_stock_issue_schemes.py
python migrate_unassigned_sales_and_salary.py
python migrate_scheme_claims_and_exports.py
python migrate_incentive_and_dayview.py
python migrate_scheme_claim_split.py
python migrate_targets.py
python migrate_target_incentive.py
```

The first adds the GST invoicing columns; the second adds attachments,
custom fields, dashboard customization, and list column customization;
the third adds Leave & Attendance (leave types, monthly attendance, and
loss-of-pay salary calculation) and attachment-type custom fields; the
fourth adds individual staff logins and walks you through creating your
first (Admin) account — **required**, since the app won't let you past
the login page without at least one account existing; the fifth switches
annual leave quotas from per-employee to shared (see section 7) — safe to
run even if you never used per-employee quotas; the sixth adds the GST
Filing tab (GST columns on Purchases, GST-scheme/reminder/email settings,
and the GSTR-2B upload table — see section 7b); the seventh adds the
"Import from Vendor File" button on the Purchases tab (see section 5);
the eighth adds Stock Issues (van/route sales), Admin-only Edit screens
for Purchases and Sales, and the Profit & Loss report (see section 5 and
section 5d); the ninth adds discounted-price sales, free-scheme units,
due-payment tracking, and company scheme-claim tracking to Stock Issue
reconciliation, plus the new Stock Issue Schemes & Dues report (see
section 5d); the tenth adds automatic GST Sale creation from reconciled
Stock Issues (billed to a system "Unassigned" customer until reassigned)
and the Salary Schedule Refresh/pro-rata feature (see section 5g); the
eleventh adds Excel export on every major list page, the new Scheme
Claims tab, and per-product Schemes (see section 7c); the twelfth adds a
per-product Incentive (₹/unit), factored into Profit & Loss as Incentive
Income, and a day-wise Cost vs Sales table on the Stock Issues Report
page that deliberately excludes Incentive (see sections 5d and 7c); the
thirteenth splits Stock Issue reconciliation's Discount ₹ (a real,
non-claimable margin reduction) apart from a new, directly-entered
Scheme Claim ₹ per line (what's actually claimable from the company),
and changes Stock Issue deletion to fully reverse a due payment/scheme
claim instead of refusing to delete (see section 5d); the fourteenth
adds the new **Targets** table backing the Sales Targets tab — monthly
targets per Salesperson (+ optional Product): Qty Sold, Sales Value ₹,
and a Discount reduction cap (see section 5h); the fifteenth adds
`Targets.BaseIncentiveAmount` and the new `TargetIncentiveBuckets` table
for the Incentive-with-over-achievement-buckets feature (see section 5h).

**No new migration needed for the "Add More Products" update (including
its line-consolidation follow-up), letting Admin re-edit a money-locked
reconciliation, Indian-style number formatting, Stock Issues column
totals, or the Reports hub's category-wise stock breakdown:** all are
display/route/logic changes on data that already exists — nothing to
run, just copy in the updated files.
(See section 3 below for login, sections 5–7c for the rest. For the
general pattern behind all of these — and what to do every time you get
a future update — see section 9, "Updating this app safely".)

---

## 2. Running the app

```
python app.py
```

You'll see:
```
Running on http://127.0.0.1:5000
```

On the same computer, open a browser to **http://127.0.0.1:5000**.

### Letting other staff on the same office network use it

The app is already listening on all network interfaces. Find this
computer's local IP address:

- Windows: open Command Prompt, run `ipconfig`, look for "IPv4 Address"
  (something like `192.168.1.25`)
- Mac/Linux: run `ifconfig` or `ip addr`, look for the LAN address

Then anyone else on the same Wi-Fi/network can open, from their own
browser:

```
http://<that-ip-address>:5000
```

for example `http://192.168.1.25:5000`. Keep the terminal window with
`python app.py` running on the host computer — that computer is acting as
the shared server for everyone else.

**Tip:** For day-to-day use, leave this running on one always-on office
PC (or a small always-on machine like a mini PC) rather than someone's
personal laptop, so the data stays available whenever staff need it.

---

## 3. Log in, add staff accounts, and appearance (dark mode)

Opening the app now shows a landing page with a **Login** button, not the
dashboard directly. Sign in with the account you created in section 1
(via `migrate_auth.py`, or the `demo`/`demo1234` account if you used
`seed_demo.py`).

- **Adding a login for each staff member** — once signed in as an Admin
  account, go to **Manage Users** (under Setup in the left menu) →
  **Add User**. Give them a username, password, and choose **Staff**
  (everyday use) or **Admin** (can also manage other users' logins) as
  their role. Share the username/password with them directly — there's
  no email step.
- **Deactivating someone** — from Manage Users, "Deactivate" blocks that
  login without deleting their history in the app; "Reactivate" restores
  it. You can't deactivate the account you're currently signed in as.
- **Resetting a forgotten password** — an Admin can reset anyone's
  password from Manage Users → Reset Password. Anyone can change their
  own password from the account menu (top-right, click your name) →
  Change Password.
- **Dark mode** — click the moon/sun icon in the top-right corner (also
  available on the landing and login pages) to switch between light and
  dark. Your choice is remembered on that browser/device; other people
  signing in from their own browser choose their own independently.
- **Logo** — the Hari Hara Ganga emblem (trident/conch/chakra mark) appears in the sidebar next
  to the app name, on the Login card, on the landing page, and as the browser-tab favicon. It's a
  static image at `static/img/logo.png` (plus a resized `static/img/favicon.png` and the original
  full lockup at `static/img/logo-full.png`) — replace that file with a new PNG of the same name
  to change it everywhere at once.
- **Collapsible side menu categories** — each category heading in the left
  menu (Inventory, Purchasing, Sales, Finance, Fleet, Payroll, Setup) is
  clickable: click it to collapse/expand the items under it, with a chevron
  showing which way it's currently folded. Collapsed/expanded state is
  remembered per browser/device (like Dark mode), and a category
  containing the page you're currently on always stays expanded even if
  you'd previously collapsed it, so navigating never hides where you are.

*Security note:* sessions are signed with a key stored in `secret_key.txt`,
created automatically the first time the app runs. Keep that file private
(don't share it or check it into version control) — anyone with it could
forge a login session. It's separate from your database, so copying just
`db/distributor.db` for a backup (section 7) never exposes it.

---

## 4. Set up your GST details before invoicing customers

Go to **Company / GST Settings** in the menu and fill in your distributor
name, GSTIN, PAN, registered address & state, phone/email, bank details
(for the invoice's payment section), and an invoice number prefix. These
appear on every GST invoice you generate. You'll also want to set an
**HSN/SAC code and GST rate** on each product (Products & Stock → Edit),
and a **State** on each customer (Customers → Edit) — the app uses your
state vs. the customer's state to automatically apply CGST+SGST
(same-state) or IGST (different state) on each sale.

## 5. Everyday use (a)

**Number formatting:** every ₹ amount throughout the app (and plain counts/quantities where it
matters, like Reports' stock totals) is now shown Indian-style — grouped in lakhs/crores, e.g.
₹8,62,000.00 — instead of the Western ₹862,000.00. This is purely a display change; nothing about
how numbers are stored or calculated is different, and it applies everywhere a rupee figure
appears: lists, detail pages, reports, exports, invoices.

Everything is reached from the left-hand menu:

- **Dashboard** — today's/this month's sales, purchases, expenses,
  receivables/payables at a glance, plus every active alert in one place.
- **Products & Stock** — add products with Min Stock / Max Stock
  thresholds, plus HSN/SAC code and GST rate for invoicing; the dashboard
  and this list automatically flag anything at or below minimum (reorder
  now) or at/above maximum (overstocked). "Adjust" lets you record stock
  found, damaged, or lost outside a normal purchase/sale.
- **Suppliers / Customers** — contact, credit, GSTIN, and state details.
- **Purchases / Sales** — multi-line invoices; saving a "Received"
  purchase or "Completed" sale automatically updates stock levels. Both
  compute a full GST breakup (CGST+SGST or IGST per line, taxable amount,
  round-off) — sales from your state vs. the customer's place of supply,
  purchases from your state vs. the supplier's GSTIN (or a manual
  "Inter-state purchase" checkbox if the supplier has no GSTIN on file). A
  purchase also has an **ITC eligible** checkbox (on by default — uncheck
  it for a blocked/personal-use purchase so it's excluded from GST Filing's
  ITC figures) and a **Reverse Charge** checkbox. These purchase-side GST
  fields feed the GST Filing tab (section 7b) — they're optional to fill
  in if you don't need that. From a saved sale, click **View / Print Invoice** for a
  statutory tax invoice you can print or save as PDF from the browser, or
  **Download PDF** for a ready-made PDF file — both include your company
  details, GSTIN, the customer's billing details, HSN/SAC per line item,
  the CGST/SGST/IGST breakup, amount in words, bank details, and terms,
  per Rule 46 of the CGST Rules. Invoice numbers auto-generate as
  `PREFIX/FY/0001` and reset each new financial year (1 April).
  *Note:* if your turnover has crossed the government e-invoicing
  threshold, invoices also need a live IRN + QR code from the GST
  e-invoice portal — that requires a separate registered API/GSP
  connection and isn't something a locally generated PDF can produce;
  everything else required on the invoice is covered.
- **Import from Vendor File** — an "Import from Vendor File" button on
  the Purchases list lets you upload a supplier's own Excel export
  instead of typing every line by hand. Pick which Supplier the file is
  from, upload it, and review a matched-up preview before anything is
  saved: each material code is auto-matched to one of your Products if
  it's been imported before, or you pick/create the Product for it; you
  can also edit the PO number, purchase date, payment status, and
  ITC/Reverse-Charge flags for each Sales Order group before confirming.
  Once matched, the app remembers that supplier's material code →
  Product mapping (in a `SupplierProductMap` table) so re-uploading a
  later file from the same supplier auto-matches more of it each time.
  **Note:** this was built against one vendor's actual file format (an
  "SO Details" sheet with columns SO Number, SO Date, Material, Material
  Code, Quantity, Sales Unit, Net Value in Rs., Status) — a differently
  laid-out export from a different vendor, or a future version of this
  vendor's format, may need the parser (`purchase_import.py`) adjusted
  for its column names. This is a web-app-only feature (like the GSTR-2B
  upload in section 7b) — there's no Access-side equivalent.
- **Operating Expenses** — rent, fuel, tolls, office costs, etc.,
  optionally tagged to a vehicle.
- **Vehicles** — registration, insurance/permit/PUC/fitness expiry dates
  (auto-flagged 30 days before expiry).
- **Maintenance** — service history per vehicle; set a "Next Due Date"
  and it's auto-flagged 7 days before.
- **Employees** — roster and monthly salary amount. Quick links on each
  employee's edit page jump to their attendance entry and leave balance
  (annual leave quotas are configured once for everyone, on the Leave
  Types page — see section 7).
- **Leave & Attendance** — see section 7 below.
- **Salary Schedule** — pick a month, click "Generate Schedule" to create
  that month's pending payment for every active employee (automatically
  deducting any active advance's monthly installment, and any loss-of-pay
  computed from that employee's attendance for the month — see section 7),
  then "Mark Paid" as each one is paid out.
- **Advance Payments** — record an advance with a repayment period; it
  auto-deducts from salary each month until the balance reaches zero.

---

## 5d. Editing Purchases/Sales, Stock Issues (van/route sales), and Profit & Loss

- **Editing a Purchase or Sale** — an **Edit** button on a Purchase's or
  Sale's detail page (next to Print Invoice, for Sales) is visible only
  to **Admin** accounts, and lets you correct a mistake — wrong quantity,
  price, product, date, supplier/customer, etc. — on an already-saved
  record instead of deleting it and starting over. Saving recalculates
  the GST breakup and replaces the stock movement the record originally
  created, so stock levels stay correct after the fix. A Sale's Invoice
  Number can't be changed on edit, so the numbering sequence and any
  already-printed invoice stay consistent — if that invoice was already
  handed to the customer, reprint it after editing so it matches. Staff
  accounts can still create new Purchases/Sales as before; only editing
  an existing one is Admin-only.
- **Stock Issues** (van/route sales) — issue a batch of products to a
  salesperson (any Active Employee) in the morning from **Stock Issues >
  Issue Stock**: pick who it's going to, the date, and each product +
  quantity + price. This deducts the issued quantities from stock right
  away, the same way a sale would. At day's end, open that issue and
  click **Reconcile**: enter how many of each product actually sold and
  how many came back unsold, plus the total cash handed in. The app
  works out the expected cash (qty sold × price, per product), compares
  it to what was actually collected, and flags a shortfall or surplus.
  Any quantity issued but not accounted for as sold or returned (damage,
  loss, giveaways, etc.) shows as "unaccounted" on the issue's summary.
  Returned quantities go back into stock automatically. **This is
  deliberately kept separate from Sales/GST** — reconciling a stock issue
  does not create a Sale record or affect GST Filing; it's a same-day
  custody/cash-tally tool for route/van salespeople, not a substitute for
  invoicing. If you also need a GST invoice for what a salesperson sold
  in a day, enter that separately as a normal Sale.
- **Re-stocking the same salesperson again the same day** — if a
  salesperson comes back for more products before you've reconciled their
  issue (a morning batch, then an afternoon top-up), open that issue and
  click **Add More Products** rather than creating a second, separate
  Stock Issue for the same day/person. The added products are deducted
  from stock immediately, just like the original issue. If a top-up adds
  more of a product that's already on this issue, it's **consolidated
  into that one product line** (Qty Issued adds up, Unit Price becomes
  the qty-weighted average of the old and new price) rather than showing
  as a separate line — so Reconcile shows one row per product for the
  whole day, not one row per top-up, with the correct combined expected
  revenue. Only a genuinely new product on this issue gets its own new
  line. This is only available until the issue is reconciled; once
  reconciled, issue a new Stock Issue for anything further, or use
  **Edit Reconciliation** (Admin only) if you need to correct what's
  already there.
- **Column totals in the Stock Issues tab** — the Stock Issues list (Expected/Collected/
  Discrepancy), an individual issue's line-item table (Qty Issued/Sold/Returned/Free, Discount ₹,
  Scheme Claim ₹, Unaccounted), and the Reconcile screen itself (same columns, live-updating as
  you type) all show a **Total** row at the bottom, summing every numeric column — except **Unit
  Price**, since adding up a per-unit price across different products isn't a meaningful figure.
- **Discounts, free scheme units, scheme claims, and dues during
  reconciliation** — the Reconcile screen has four extra fields per
  product line:
  - **Discount ₹** — the total discount given on the units sold at a
    lower price (expected cash for that line is qty sold × price, minus
    the discount). This is a **real margin reduction the distributor
    absorbs** — it is never counted as something claimable back from the
    company.
  - **Qty Free (scheme)** — units handed out at no charge under a company
    scheme; these reduce stock like a sale but contribute nothing to
    expected cash.
  - **Scheme Claim ₹** — the actual rupee amount claimable back from the
    company for that line's scheme. It starts pre-filled as a guess (Qty
    Free × Unit Price), but is a plain, directly-editable figure, since
    the company's real reimbursement rate need not match the line's
    retail price — enter whatever the company actually owes, if it's a
    scheme that's reimbursed at all.
  - A free-text **Comments** box to note why (scheme name, reason, etc.).

  If the cash collected doesn't cover the full expected amount, the
  shortfall is saved as that issue's **Amount Due** with a
  Paid/Partial/Unpaid status, rather than only shown as a one-time
  discrepancy — open the issue later and use **Record Due Payment** to
  log each collection against it (with its own date and notes) until it's
  fully settled; this also updates that issue's **Discrepancy** figure to
  match what's still outstanding, clearing to ₹0 once fully collected
  rather than staying stuck at the shortfall recorded at reconciliation
  time. Every line's **Scheme Claim ₹** rolls up into that issue's
  **Scheme Amount** — the total actually claimable back from the company
  — which you can mark **Claimed** (when you've submitted the claim) and
  then **Received** (when the company actually pays it) from the issue's
  page. (Older issues reconciled before this feature keep the old
  combined figure — discount + free-unit value — as their Scheme Amount
  until you re-reconcile them via **Edit Reconciliation**.)
- **Deleting a Stock Issue** (Admin only, from the issue's own page) — if
  a due payment and/or scheme claim has already been recorded against it,
  deleting no longer refuses outright: it performs a full reversal
  instead, erasing that due-payment history and resetting the claim
  status along with everything else the delete already undoes (stock
  impact, the linked auto-created Sale). You'll get a stronger
  confirmation prompt in that case since payment/claim history is being
  erased, not just the issue itself — review it manually first if you're
  not sure that's what you want.
- **Re-editing (Edit Reconciliation) an issue with a due payment or scheme
  claim already recorded** (Admin only) — this also no longer refuses
  outright. Opening **Edit Reconciliation** on such an issue shows a
  choice right on the form: **Keep it** (the default — leaves the
  existing due-payment history and claim status untouched, only the
  sold/returned/free/discount/scheme-claim figures and cash collected get
  corrected; re-enter the *full* amount collected so far, including any
  due payments already received, in Total Cash Collected) or **Clear it
  and start fresh** (erases the existing due-payment records and resets
  the claim status back to Not Claimed, so you can re-record them
  accurately against the corrected figures). Pick "Clear it" whenever the
  correction changes what was actually owed or claimable — otherwise the
  old due-payment/claim amounts would no longer line up with the
  corrected figures.
- **Stock Issue Schemes & Dues report** (Stock Issues > Stock Issue
  Schemes & Dues) — a date-range report totaling expected/collected
  amounts, outstanding dues by salesperson, pending company scheme claims,
  and every individual discount/free-scheme line, so nothing given away or
  still owed gets lost track of. It also has a **Day-wise Cost vs Sales**
  table at the bottom — for each day in the range, qty issued × each
  product's Cost Price vs. that day's Stock Issue Expected/Collected
  amounts. This table is a plain cost-vs-sales check and deliberately
  does **not** factor in the per-product Incentive (see below) — for the
  margin view that does account for Incentive, use Profit & Loss. It also
  carries per-day totals of **Qty Sold, Discount ₹, Scheme ₹, and
  Unaccounted** qty (issued minus sold, returned and free), summed across
  all lines for that day, so you can see sell-through and leakage at a
  glance without opening each issue. The
  Dashboard also shows running "Van Sales Due" and "Scheme Claims
  Pending" totals.
- **Profit & Loss** — a computed report (Finance > Profit & Loss) over any
  date range, built entirely from what's already in the app: Revenue
  from completed Sales, Cost of Goods Sold from what sold multiplied by
  each product's *current* cost price, **Incentive Income** from what
  sold multiplied by each product's Incentive per unit (see section 7c),
  Operating Expenses by category, and Salary Payouts marked Paid, rolling
  up to Gross Profit (Revenue − COGS), then Net Profit (Gross Profit +
  Incentive Income − Operating Costs). Nothing is entered here — it's a
  working-paper view, not a statutory filing, and (like GSTR-3B's set-off
  simplification) doesn't attempt full double-entry bookkeeping,
  depreciation, or accrual adjustments. Because it uses each product's
  *current* cost price (and current Incentive) rather than what applied
  at the time of a past sale, treat older periods as a close estimate if
  those figures have changed since — the same caveat already applies
  elsewhere in the app to anything computed from a live-updating Products
  table rather than a stored-at-the-time snapshot.

---

## 5e. Bulk-uploading Customers and Sales from Excel

Unlike the vendor-file import (section 5, which matches one specific
supplier's own file format), these two work off **app-provided
templates** — there's no existing file format to match, so you download
a template, fill it in, and upload it back.

- **Customers** — on the Customers page, click **Import from Excel**,
  download the template, fill in one row per customer (Customer Name is
  the only required column), and upload it. A row whose Customer Name
  exactly matches (case-insensitive) an existing customer **updates**
  that customer's details from the file; anything else is **added** as a
  new customer. This is a one-step upload — there's nothing to
  match/review, since matching is just by name.
- **Sales** — on the Sales page, click **Import from Excel**, download
  the template, fill in one row per sale line (Invoice Number, Date,
  Customer Name, Product, Qty, Rate, and optionally Payment Status /
  Amount Received), and upload it. Rows sharing the same **Invoice
  Number** become one multi-line Sale; leave Invoice Number blank for a
  single-line sale (each blank-invoice row becomes its own Sale). Like
  the Purchases vendor-file import, this is a two-step flow: upload
  shows a **review page** first, where each row's Customer and Product
  are auto-matched by exact name if possible — pick from the dropdown,
  or create a new Customer/skip a line, before confirming. Once
  confirmed, each Sale is created exactly the way a manually entered one
  is: full GST breakup (CGST/SGST or IGST based on the customer's
  state), stock reduced, and it shows up in GST Filing and Profit & Loss
  like any other sale.

Both of these need no separate migration — they only need `openpyxl`,
which is already required by the GSTR-2B and vendor-file-import features
(it's in `requirements.txt`).

---

## 5f. Stock Issue corrections, and Admin-only access controls

- **Delete a Stock Issue** (Admin only) — on a Stock Issue's page, a
  **Delete** button lets you remove one that was entered by mistake. This
  fully reverses its stock impact (issued units go back, any returned
  units come back out, any free-scheme units come back in) so inventory
  stays correct. To protect the money trail, deletion is **blocked** if a
  due payment has already been recorded against the issue, or its scheme
  claim has already been marked Claimed/Received — review those cases
  manually instead of deleting.
- **Edit an already-reconciled Stock Issue** (Admin only) — an **Edit
  Reconciliation** button reopens the Reconcile screen, pre-filled with
  what was last saved, for an issue that's already Reconciled. Saving
  replaces the qty sold/returned/free, discount, comments, and cash
  collected figures entirely and recomputes everything from scratch —
  same protection applies: blocked if a due payment or scheme claim has
  already been recorded, for the same reason as delete above.
- **Unit price not filling in for some products (fixed)** — issuing
  stock to a salesperson auto-fills each line's price from the product's
  selling price. Products auto-created by the Purchases "Import from
  Vendor File" feature previously got a ₹0 selling price (since a
  purchase file only has cost, not a selling price) — new products
  created that way now default to their purchase cost instead of ₹0, and
  the Issue Stock screen shows a warning under any line where the price
  is still ₹0, prompting you to type in the correct one. Review pricing
  on the Inventory page for any product you're not sure about.
- **Access controls — Admin-only modules.** The following are now
  visible and usable only to Admin accounts (Staff accounts no longer see
  them in the sidebar, and are redirected if they try the URL directly):
  Purchases, Suppliers, GST Filing, Profit & Loss, Employees, Salary
  Schedule, Advance Payments, and every page under Setup (Company/GST
  Settings, Custom Fields, Customize Dashboard, Manage Users). Leave &
  Attendance, Customers, Sales, Stock Issues, Inventory, Expenses, and
  Fleet stay available to Staff as before.
- **Access controls — delete buttons.** Every delete action in the app
  (attachments on any tab, custom field definitions, GSTR-2B uploads,
  leave types, and the new Stock Issue delete above) is now Admin-only —
  the delete button itself is hidden from Staff accounts, and the
  underlying action is blocked server-side even if attempted directly.

---

## 5g. Reconciled stock issues as Sales, the "Unassigned" bucket, and Salary Refresh

- **Reconciling a Stock Issue now also creates a real GST Sale.** When you
  reconcile (or Admin-re-edit) a Stock Issue, the units marked "sold" are
  now billed as a proper GST Sale — same CGST/SGST/IGST computation,
  invoice number, and inclusion in GST Filing and Profit & Loss as any
  other sale — in addition to the existing same-day cash-tally on the
  Stock Issue itself. (This is a change from the "deliberately kept
  separate from Sales/GST" note in section 5d — that note now only applies
  to units that were returned or given free, which still don't generate a
  Sale.) The Sale is billed to a single system customer called
  **"Unassigned (Van/Route Sales)"** rather than any real customer, since
  a Stock Issue doesn't record which end customer actually bought each
  unit. No stock is deducted a second time — the Stock Issue's own
  deduction already covers it.
- **Reassigning an Unassigned sale to real customer(s)** — open the sale
  (from Sales, or the Dashboard warning below) and click **Reassign**. Each
  product line's quantity can be **broken down across multiple
  customers** — e.g. if 20 units of a product were sold that day, you can
  split those 20 as 8 to one customer, 7 to another, and leave 5 unassigned
  for later, all from the same page. For each split, enter the quantity
  and pick one of your existing customers, or type a new customer's name
  and details directly on that same page — no need to add them to
  Customers first. Click **Add split** to add another quantity/customer
  row for a line. Splits going to the same new customer (matched by name +
  phone) in one submission are grouped into a single new Sale for that
  customer; any quantity you don't assign a split to stays behind on a
  (now smaller) Unassigned sale you can come back and reassign later. A
  running note under each line shows how much of it is still unassigned,
  and a quantity typed in beyond what's left on the line is automatically
  capped so you can't over-allocate it. Reassigning recomputes the GST
  breakup for each customer's actual state (so it correctly flips between
  CGST+SGST and IGST if they're in a different state than your business).
- **Dashboard warning** — whenever there are sales still billed to
  Unassigned, the Dashboard shows a warning banner with the count and
  total amount, and a **Review →** button straight to a filtered Sales
  list showing just those.
- **Deleting a Stock Issue** now also deletes its linked Sale (if one was
  created), keeping the two in sync; recording a Due Payment against the
  Stock Issue also updates the linked Sale's payment status/amount.
- **Salary Schedule "Refresh"** — a new **Refresh** button (Admin only,
  next to Generate Schedule) re-pulls every active employee's advances and
  recalculates their pay for that month, for any row that **isn't marked
  Paid yet** (Paid rows are left completely untouched). Use this if an
  advance was recorded, or attendance/leave was entered or corrected,
  *after* you already generated the month's schedule — Refresh brings
  Pending rows up to date without you needing to delete and regenerate.
  It also creates a row for any employee who doesn't have one yet for that
  month, same as Generate Schedule does.
- **Pay is now pro-rated by days actually worked**, not just full-month
  minus loss-of-pay days. The formula is: (days in the month − days
  before the employee's Join Date that month − loss-of-pay days from
  attendance) × (Monthly Salary ÷ days in the month). An employee who
  joined partway through a month is now correctly paid only from their
  Join Date onward; someone with a full month and no LOP is unaffected
  (same result as before).

*Design note:* Stock Issue prices were originally entered as flat rupee
figures with no GST concept (since the issue itself stays outside GST
Filing). The auto-created Sale now treats those same prices as
**GST-exclusive** — consistent with how prices work everywhere else in
this app for Sales — so the Sale's total (taxable value + GST) will be
somewhat higher than the Stock Issue's own "Expected"/"Cash Collected"
figures whenever the products carry a GST rate. This is intentional, not
a bug, but worth knowing if you compare the two figures.

---

## 5h. Sales Targets

A **Targets** tab (Stock Issues > Targets in the menu) lets you set monthly sales targets to drive
the business, and see live progress against them everywhere it matters.

- **Setting a target** (Admin only) — pick a Salesperson, an optional Product (leave it as "All
  Products" for a whole-employee target), a Year and Month, then enter: **Qty Sold Target**,
  **Sales Value Target (₹)**, and a **Discount reduction cap** — shown two ways side by side, a
  fixed ₹ ceiling for the month and a ₹/unit rate (whose effective allowance scales with actual
  Qty Sold), so you can track discount discipline either as a flat budget or per-unit. One target
  per Employee + Product + Month; saving again for the same combination updates it in place rather
  than creating a duplicate.
- **Actuals are always live** — nothing on the Targets tab is a stored snapshot. Qty Sold,
  Discount ₹, and (for a whole-employee target) Expected/Collected all come straight from Stock
  Issues/reconciliation at the moment you view the page, so a target never goes stale when a
  reconciliation is corrected later. A product-level target's "Collected" figure is allocated
  proportionally from each Stock Issue's actual cash collected (since cash is recorded once per
  issue, not per product line).
- **MTD / WTD / day-wise trend** — rather than storing separate week/day targets, the Targets tab
  spreads the month-level target evenly across the days in that month ("pace") and compares actual
  Qty Sold against that pace: a Month-to-Date card, a Week-to-Date card (current month only), and a
  day-wise cumulative-actual-vs-pace-target table with a progress bar per day.
- **Breakdowns** — filter by Salesperson (or view all combined), and see every target for the
  selected month in one table with both full-month and MTD actual-vs-target figures, including the
  "% of Qty Sold with no discount" figure.
- **Reflected elsewhere** — a small Target-vs-Actual badge/box shows up wherever the relevant
  actuals already appear: the **Dashboard** (a combined MTD progress card across all salespeople
  with a whole-month target), the **Reports hub** (a Sales Targets card), the **Stock Issues list**
  (a per-row "Month Target" MTD % badge for that issue's salesperson), and the **Stock Issue
  view/Reconcile screens** (a target-progress line for that salesperson's month, so you can see
  pace and discount-cap usage while reconciling). All of this is read-only outside the Targets tab
  itself — editing a target always happens there.

No target is required for the app to work as before — Stock Issues, Reconciliation, and everything
else function exactly the same with zero targets set; the Target-progress indicators simply don't
appear until at least one target exists for the relevant salesperson/month.

- **Incentive with over-achievement buckets** — each Target also has a **Base Incentive (₹)**
  field and an editable table of **over-achievement buckets**: rows of "Achievement % ≥ →
  Multiplier ×" (e.g. 100%→1.0x, 110%→1.1x, 125%→1.25x). Achievement % is Qty Sold actual ÷ Qty
  Sold Target × 100; the **highest** threshold reached wins, and the final Incentive Earned = Base
  Incentive × that bucket's multiplier. Reaching none of the configured thresholds means no
  incentive for that period — a target with an empty bucket table never pays out, so add at least
  a 100%→1.0x row as the baseline if you want a "target met" payout. Add or remove bucket rows
  freely with the **Add Bucket** button before saving; buckets are fully replaced on every save.
  Incentive Earned (both full-month and MTD) shows per target on the Targets table, and combined
  MTD incentive also rolls up into the Dashboard and Reports hub Sales Target cards.

---

## 6. Everyday use (b) — attachments, custom fields, and customizing your views

- **Attachments** — Employees, Advance Payments, Vehicles, Purchases, and
  Expenses each have an Attachments section (on the record's edit/detail
  page) where you can upload receipts, RC/insurance copies, ID proofs,
  photos, etc., and download or delete them later. Up to 25MB per upload.
- **Custom fields** — go to **Custom Fields** in the left menu to add your
  own extra fields (text, number, date, dropdown, checkbox, or
  **attachment**) to any tab — Products, Suppliers, Customers, Purchases,
  Sales, Expenses, Vehicles, Maintenance, Employees, Salary, or Advances.
  Once added, the field appears automatically on that tab's add/edit form
  and on its detail view (where one exists). An attachment-type custom
  field lets you upload a file against that field on any record, the same
  way the built-in Attachments section works — you must save the record
  once before its upload option appears. "Hide" keeps a field's stored
  data but removes it from forms; "Delete" permanently removes the field
  and everything stored in it, including any uploaded files.
- **Customize Dashboard** — go to **Customize Dashboard** in the left menu
  to choose which dashboard widgets (low stock, overstock, maintenance
  due, documents expiring, salary status, active advances, GST filing due
  dates, the summary stat cards) are shown, and the order they appear in.
- **Customize Columns** — every list page (Products, Suppliers, Customers,
  Purchases, Sales, Expenses, Vehicles, Maintenance, Employees, Salary
  Schedule, Advance Payments) has a "Customize Columns" button to show,
  hide, and reorder that page's columns.

---

## 7. Leave & Attendance and Loss-of-Pay salary

Go to **Leave & Attendance** in the left menu:

- **Leave Types** — configure the leave types staff can take (Sick Leave
  and Casual Leave are set up by default) and each one's **Annual Quota**
  (days/year). The quota is shared: whatever you set for a leave type
  applies to every active employee alike — there's no per-employee
  configuration. Add more types, rename, or deactivate one here
  (deactivating keeps its history but hides it from new attendance
  entry); each row also has an inline field to update its quota, which
  takes effect immediately for everyone going forward.
- **Attendance entry** — a monthly summary per employee: enter Present
  Days, Weekly Off Days, and days taken per leave type for the month, and
  save. You don't fill in a daily calendar — just the totals for the
  month.
- **Automatic Loss-of-Pay (LOP)** — when you save a month's attendance,
  the app splits each leave type's days into "Paid" (covered by that
  employee's remaining balance of the shared annual quota for that leave
  type, tracked cumulatively through the year for that employee) and
  "Unpaid" (days taken beyond it). LOP days for the month = days in month
  − present days − weekly off days − total paid leave days. This LOP
  figure then automatically reduces that employee's pay when you generate
  the Salary Schedule for the same month: each LOP day deducts one day's
  pay, calculated as Monthly Salary ÷ days in that month. If no
  attendance was entered for an employee/month, salary generation falls
  back to full pay, same as before this feature existed.
- **Leave Balance** — a year-by-year view of every active employee's
  quota (the same figure for everyone), days used, and remaining balance
  per leave type.

*Note:* once a month's attendance is saved, its Paid/Unpaid leave split
is locked in based on quota usage up to that point. If you later go back
and edit an earlier month or change a leave type's shared quota,
already-saved later months are not automatically recalculated — re-save
them if you need the change reflected.

---

## 7b. GST Filing (GSTR-1 / GSTR-2B / GSTR-3B reference reports and reminders)

Go to **GST Filing** in the left menu. Everything here is a **working
reference computed from your own Sales and Purchases records** — it isn't
fetched from, and isn't submitted to, the GST portal, and it isn't a
substitute for actually filing there or for advice from your tax
professional. Due dates are set by the Government of India and can change
by notification — always double-check on
[services.gst.gov.in](https://services.gst.gov.in) before relying on
anything shown here.

- **GSTR-1** — a monthly outward-supplies summary computed from your
  Sales: invoice count, taxable value, tax, a B2B (customer has a GSTIN)
  vs. B2C split, a rate-wise summary, an HSN-wise summary, and the
  underlying invoice list. Use the Prev/Next buttons to move between
  months.
- **GSTR-2B** — the government's own auto-drafted ITC statement can't be
  fetched automatically (there's no free API for it), so instead: download
  the GSTR-2B Excel file for a month from the GST portal (Services →
  Returns → Returns Dashboard → GSTR-2B → Download Excel) and upload it
  here. It's parsed for the Integrated/Central/State tax ITC totals used
  on the GSTR-3B page. **Parsing is best-effort** — it looks for the
  standard B2B/CDNR/ISD/IMPG sheet layout GSTN documents, but hasn't been
  verified against a real downloaded file in the environment this was
  built in. Always spot-check the parsed total against the "Summary"
  sheet in your actual file, especially the first time — if the layout
  doesn't match, the upload fails loudly (marked "Failed" with an
  explanation) rather than showing a wrong number. The original file is
  always kept and downloadable.
- **GSTR-3B** — a monthly summary combining outward tax liability (from
  Sales, Table 3.1) with ITC (Table 4): once you've uploaded a GSTR-2B for
  a month, that figure is used (current GST law limits what you can claim
  to what's in GSTR-2B); until then, it falls back to "ITC as per our
  purchase records" from Purchases marked ITC-eligible, as a secondary
  cross-check. Shows a simplified net payable per tax head — the actual
  GST set-off rules (which let IGST credit offset CGST/SGST payable too)
  are more involved than this working paper models; the GST portal
  computes the real cross-utilised figure when you file.
- **Due dates & reminders** — the GST Filing page lists upcoming due dates
  for GSTR-1, GSTR-3B, GSTR-2B (informational — nothing to file, just a
  reminder to go check it), and PMT-06 (if on QRMP), based on the filing
  scheme and state set below. These also show as a Dashboard widget and a
  sidebar badge when something's due soon.
- **Filing scheme & email reminders (Company / GST Settings page)** —
  choose **Monthly** or **QRMP** filing; under QRMP, the GSTR-3B due date
  (22nd vs. 24th of the month after the quarter) is worked out
  automatically from your registered State. Turn on **email reminders**
  and add recipient email(s) to also get an email when a due date is
  approaching (in-app alerts always work regardless) — this needs SMTP
  settings filled in (host/port/username/password/from-address; for
  Gmail and most providers, the password field needs to be an
  app-specific password, not your normal login password) and only sends
  while `python app.py` is running, same as the rest of this app. Use
  **Send Test Email** on that page to confirm it's working before relying
  on it.

---

## 7c. Reports hub, Excel/PDF exports, Scheme Claims, and per-product Schemes

- **Reports** (in the left menu, right under Dashboard, and also a
  **Reports & Exports** button on the Dashboard itself) — a hub page with
  one card per tab (Sales, Purchases, Products & Stock, Operating
  Expenses, Stock Issues, Salary, Scheme Claims, Customers, Fleet), each
  showing a quick numeric breakdown for that tab — this month's total and
  count, a top-5 or by-category breakdown, a short trend where relevant
  (e.g. Sales' last few months) — plus **Excel** and **PDF** export
  buttons for that tab's full data and a link straight to the tab itself.
  Admin-only tabs (Purchases, Salary, Scheme Claims, and the Suppliers/
  Employees/Advances exports) only show their cards to Admin accounts,
  same as elsewhere in the app. The **Products & Stock** card also shows
  **Total Qty Stock broken down by category** (alongside the overall
  total) — e.g. "Snacks: 8,62,000, Beverages: 500" — so you can see stock
  spread across categories at a glance, not just the overall stock value.
- **Export to Excel / Export to PDF** — every major list page (Products,
  Suppliers, Customers, Purchases, Sales, Stock Issues, Expenses,
  Vehicles, Maintenance, Employees, Salary Schedule, Advance Payments,
  Scheme Claims) has both an **Export to Excel** button (.xlsx, with
  friendly column headers) and a **PDF** button (the same data, formatted
  as a printable table) — handy for further analysis, sharing, printing,
  or a one-off report outside the app. Both always export the complete
  list (not just whatever's currently filtered on screen).
- **Scheme Claims** (Reports > Scheme Claims, Admin only) — a place to
  manually track distributor-level scheme claims that aren't tied to any
  single product or Stock Issue — for example an annual volume rebate, or
  a company-wide promotional claim covering several products or months.
  Add a claim with a Scheme Name, the date, which product(s)/line it
  applies to (free text), a description of the scheme's terms, and the
  amount you're claiming. As it moves along, mark it **Claimed** (once
  you've submitted it to the company), then **Received** (enter the
  amount actually paid, in case it differs from what was claimed), and
  finally **Completed** once you're done with it. This is separate from,
  and doesn't change, the existing per-Stock-Issue discount/free-unit
  scheme tracking on the Stock Issue Schemes & Dues report (section 5d) —
  that one stays exactly as it was, for schemes tied to a specific day's
  van/route sales.
- **Product-level Schemes** — on a product's Add/Edit page, you can set a
  **Scheme Name** and a **Scheme %** (of Cost Price) to represent a
  running discount/promo on that product (e.g. "Diwali Scheme, 10% of
  cost price"). Wherever that product's price is used, the app now
  automatically suggests the scheme-discounted figure — always still
  editable before you save:
  - **Issue Stock** and **New Sale** — selecting a product with a Scheme %
    set auto-fills the price as (Selling Price − Scheme % of Cost Price)
    instead of the full Selling Price, with a note showing what the full
    price and discount were.
  - **Stock Issue reconciliation** — the **Discount ₹** field for a
    scheme product is auto-suggested as (Cost Price × Scheme %) × Qty
    Sold as you type in the quantity sold, with a hint showing the
    per-unit suggestion. Once you type into that field yourself, your
    figure is kept and no longer auto-overwritten (including when
    re-opening an already-reconciled issue for editing).
  The Products & Stock list shows each product's active scheme (if any)
  as a badge in its own column.
- **Product-level Incentive** — on a product's Add/Edit page, an
  **Incentive per Unit (₹)** field lets you record the extra ₹ the
  distributor earns per unit sold on top of the normal margin (separate
  from Cost Price, Selling Price, and Scheme). It flows straight into
  **Profit & Loss** as an **Incentive Income** line — units sold in the
  report's date range × Incentive per unit — added on top of Gross Profit
  before Net Profit (see section 5d). It is deliberately left out of the
  Stock Issue Schemes & Dues report's Day-wise Cost vs Sales table, which
  is meant as a plain cost-vs-sales check rather than a margin view. The
  Products & Stock list shows each product's Incentive in its own column,
  and it's included in the Products Excel/PDF export.

---

## 8. Backing up your data

Everything — including every login account — lives in one file:
`db/distributor.db`. To back up, just copy that file somewhere safe (a
USB drive, cloud storage folder, etc.) — ideally on a daily schedule. To
restore, stop the app, replace the file with your backup copy, and
restart. Don't back up `secret_key.txt` alongside it anywhere it could be
exposed (see the security note in section 3) — it's not needed to restore
your data, only to keep existing browser sessions valid.

---

## 9. Updating this app safely (without losing data)

Every update to this app — including this one — is built so that
installing it never deletes or overwrites anything you've already
entered. The full explanation, and the checklist to follow every time you
receive a new version, is in **`UPDATING_SAFELY.md`** in this folder.
Short version:

1. Back up `db/distributor.db` first, always (see section 8) — this takes
   a few seconds and is your safety net no matter what else you do.
2. Unzip the new version into a **separate, new folder** — don't extract
   it directly on top of your current one.
3. Copy your existing `db/distributor.db`, `uploads/` folder, and
   `secret_key.txt` from your old folder into the new one.
4. Run every `migrate_*.py` script listed in that update's README you
   haven't run yet, in the order listed — each one only *adds* new
   tables/columns/fields and leaves everything else exactly as it was;
   none of them delete or overwrite existing rows.
5. Start the app from the new folder and spot-check a record you know
   existed before the update, to confirm it still looks right (existing
   data intact, new fields empty/default until you fill them in).

That's the same process this update (and every `migrate_*.py` script
shipped so far) was built and tested against — see `UPDATING_SAFELY.md`
for the reasoning, the full checklist, and what to do if something looks
wrong.

---

## 10. Files in this folder

```
app.py                          Flask application (all routes/business logic)
db.py                           Database connection helpers
db/schema.sql                   Table definitions
init_db.py                      Run once to create an empty database
migrate_gst_invoice.py          Run once to add GST invoicing to an existing database
migrate_customization.py        Run once to add attachments/custom fields/dashboard & column customization
migrate_leave_attendance.py     Run once to add Leave & Attendance and attachment-type custom fields
migrate_auth.py                 Run once to add staff logins and create your first (Admin) account
migrate_shared_leave_quota.py   Run once to switch annual leave quotas from per-employee to shared
migrate_gst_filing.py           Run once to add the GST Filing tab (Purchases GST columns, GST/SMTP settings, GSTR-2B table)
migrate_purchase_import.py      Run once to add the "Import from Vendor File" button on Purchases
migrate_stock_issues.py         Run once to add Stock Issues, Admin-only Purchase/Sale editing, and the P&L report
migrate_stock_issue_schemes.py  Run once to add discounts/free scheme units/due tracking/company claims to Stock Issues
migrate_unassigned_sales_and_salary.py  Run once to add auto-created Sales from reconciled Stock Issues (Unassigned bucket) and Salary Refresh
migrate_scheme_claims_and_exports.py    Run once to add Excel export, the Scheme Claims tab, and per-product Schemes
migrate_incentive_and_dayview.py        Run once to add per-product Incentive (₹/unit)
migrate_scheme_claim_split.py           Run once to add per-line Scheme Claim ₹ (separate from Discount ₹)
migrate_targets.py                      Run once to add the Targets table (Sales Targets tab)
migrate_target_incentive.py             Run once to add Incentive + over-achievement buckets to Targets
invoice_pdf.py                  Builds the downloadable GST invoice PDF
gst_logic.py                    GST Filing business logic: due dates, GSTR-1/GSTR-3B summaries, GSTR-2B Excel parsing
gst_reminders.py                Sends GST due-date reminder emails (background thread + SMTP)
purchase_import.py              Parses a vendor's "SO Details" Excel export for the Purchases import feature
bulk_import.py                  Builds/parses the Customer and Sales bulk-upload Excel templates
seed_demo.py                    Optional: fill with sample data (includes a demo login)
templates/                      Page templates (includes invoice.html, the printable invoice)
static/                         CSS and JS (includes list-columns.js for customizable list columns)
uploads/                        Uploaded attachment files, including GSTR-2B uploads (created automatically on first upload)
secret_key.txt                  Session signing key, created automatically on first run — keep private
requirements.txt                Python packages needed
UPDATING_SAFELY.md              How this app protects your data across updates, and the checklist to follow
```
