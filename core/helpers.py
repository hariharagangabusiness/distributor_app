"""Shared non-route helpers: dates, company settings, stock/alert queries, targets, location constants."""
import os
from core.auth import get_current_user
from zoneinfo import ZoneInfo



import calendar
import db
from datetime import date, datetime, timedelta
from flask import request



# Master on/off switch for the mobile-friendly UI rollout (card-view tables on
# phones instead of horizontal-scrolling ones, and taller touch targets for
# buttons/tabs on small screens) - currently live on the Finance and Sales
# modules. Flip this to False and redeploy to instantly revert every page
# back to the old plain-table behaviour app-wide, with no template changes
# needed - it's read by base.html (sets a body class) and by the templates
# that opt individual tables into card view.
MOBILE_FRIENDLY_UI_ENABLED = True


# Master on/off switch for the List/Grid view switch on record-list pages
# (Customers, Products, Suppliers, Purchases, Sales, Expenses, Vehicles,
# Maintenance, Employees, Advances, Stock Issues, Scheme Claims, Users).
# Flip this to False and redeploy to instantly pull the feature from every
# page app-wide, with no template changes needed - it's read by base.html,
# which only loads the toggle script when this is True. Without that script,
# a table's data-view-toggle attribute and the .view-grid CSS are both
# inert, so every list falls straight back to its original plain-table look,
# even for a browser that had already saved a "grid" preference.
LIST_VIEW_TOGGLE_ENABLED = True


def format_date_dmy(value):
    """Reformats a date/datetime for display as DD-MM-YYYY (DD-MM-YYYY HH:MM for a
    value that carries a time component). Storage and every <input type="date"> value
    stay ISO YYYY-MM-DD underneath - HTML5 date inputs require that format and it sorts
    correctly - this only changes what's rendered as plain text. Accepts a date/datetime
    object, an ISO string ('YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'), or None/''/already-
    non-ISO text, which all pass through unchanged rather than raising, since a table
    row's date column is sometimes blank or (rarely) already formatted."""
    if value is None or value == "":
        return value
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y %H:%M") if (value.hour or value.minute) else value.strftime("%d-%m-%Y")
    if isinstance(value, date):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, str):
        s = value.strip()
        for fmt, out in (("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M"), ("%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M"),
                         ("%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"), ("%Y-%m-%d", "%d-%m-%Y")):
            try:
                return datetime.strptime(s, fmt).strftime(out)
            except ValueError:
                continue
        return value
    return value


def sale_due_amount(sale):
    """What should actually be collected from the customer for this sale.

    Under Reverse Charge, the customer remits GST straight to the government
    rather than paying it to us - so the amount we're actually owed is just
    the taxable (product) value, not the GST-inclusive invoice total. For a
    normal (non-reverse-charge) sale, the full GST-inclusive TotalAmount is
    what's due, same as before. The invoice itself always still shows the
    full GST breakup either way - this only affects what counts as
    receivable/balance-due for collection purposes. Accepts a sqlite3.Row or
    dict with at least ReverseCharge/TaxableAmount/TotalAmount."""
    if sale["ReverseCharge"]:
        return sale["TaxableAmount"] or 0
    return sale["TotalAmount"] or 0


def sale_balance_due(sale):
    """Balance still owed on a sale: sale_due_amount() minus AmountReceived,
    rounded to paise. Accepts a sqlite3.Row or dict with AmountReceived too."""
    return round(sale_due_amount(sale) - (sale["AmountReceived"] or 0), 2)


MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def get_company_settings():
    return db.query("SELECT * FROM CompanySettings WHERE SettingsID=1", one=True)


def today_str():
    return date.today().isoformat()


def get_products_with_stock(active_only=True):
    sql = """
        SELECT p.*, COALESCE(t.CurrentStock, 0) AS CurrentStock,
               s.SupplierName AS DefaultSupplierName
        FROM Products p
        LEFT JOIN (
            SELECT ProductID, SUM(QtyChange) AS CurrentStock
            FROM InventoryTransactions GROUP BY ProductID
        ) t ON t.ProductID = p.ProductID
        LEFT JOIN Suppliers s ON s.SupplierID = p.DefaultSupplierID
    """
    if active_only:
        sql += " WHERE p.Active = 1"
    sql += " ORDER BY p.ProductName"
    return db.query(sql)


def low_stock_products():
    return [p for p in get_products_with_stock() if p["CurrentStock"] <= p["MinStock"]]


def over_stock_products():
    return [p for p in get_products_with_stock()
            if p["MaxStock"] > 0 and p["CurrentStock"] >= p["MaxStock"]]


def upcoming_maintenance(days=7):
    sql = """
        SELECT vm.*, v.RegistrationNumber, v.VehicleType
        FROM VehicleMaintenance vm
        JOIN Vehicles v ON v.VehicleID = vm.VehicleID
        WHERE vm.MaintenanceID IN (
            SELECT vm2.MaintenanceID FROM VehicleMaintenance vm2
            WHERE vm2.VehicleID = vm.VehicleID AND vm2.ServiceType = vm.ServiceType
            ORDER BY vm2.ServiceDate DESC LIMIT 1
        )
        AND vm.NextDueDate IS NOT NULL
        AND date(vm.NextDueDate) <= date('now', ?)
        ORDER BY vm.NextDueDate
    """
    return db.query(sql, (f"+{days} days",))


def expiring_documents(days=30):
    sql = """
        SELECT VehicleID, RegistrationNumber, 'Insurance' AS DocType, InsuranceExpiry AS ExpiryDate FROM Vehicles
        WHERE InsuranceExpiry IS NOT NULL AND date(InsuranceExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'Permit', PermitExpiry FROM Vehicles
        WHERE PermitExpiry IS NOT NULL AND date(PermitExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'PUC', PUCExpiry FROM Vehicles
        WHERE PUCExpiry IS NOT NULL AND date(PUCExpiry) <= date('now', ?)
        UNION ALL
        SELECT VehicleID, RegistrationNumber, 'Fitness', FitnessExpiry FROM Vehicles
        WHERE FitnessExpiry IS NOT NULL AND date(FitnessExpiry) <= date('now', ?)
        ORDER BY ExpiryDate
    """
    d = f"+{days} days"
    return db.query(sql, (d, d, d, d))


def pending_salary_this_month(year=None, month=None):
    today = date.today()
    year, month = year or today.year, month or today.month
    return db.query("""
        SELECT sp.*, e.EmployeeName FROM SalaryPayments sp
        JOIN Employees e ON e.EmployeeID = sp.EmployeeID
        WHERE sp.SalaryYear=? AND sp.SalaryMonth=? AND sp.Status='Pending'
        ORDER BY e.EmployeeName
    """, (year, month))


def employees_missing_salary_record(year=None, month=None):
    today = date.today()
    year, month = year or today.year, month or today.month
    return db.query("""
        SELECT e.* FROM Employees e
        WHERE e.Status='Active' AND e.EmployeeID NOT IN (
            SELECT EmployeeID FROM SalaryPayments WHERE SalaryYear=? AND SalaryMonth=?
        )
    """, (year, month))


def _selected_month_from_request():
    """Resolve the (year, month) a dashboard/report page should show from ?year=&month=
    query args, defaulting to the current month. Also returns the month's date range,
    prev/next (year, month) pairs, and whether the selection is the current month —
    everything the "back to previous months" nav control needs. Future months beyond
    the current one are not allowed (clamped back to today's month)."""
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if month < 1 or month > 12:
        year, month = today.year, today.month
    if (year, month) > (today.year, today.month):
        year, month = today.year, today.month
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    is_current = (year, month) == (today.year, today.month)
    return {
        "year": year, "month": month,
        "month_start": month_start.isoformat(), "month_end": month_end.isoformat(),
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "is_current": is_current,
    }


def active_advances():
    return db.query("""
        SELECT a.*, e.EmployeeName FROM AdvancePayments a
        JOIN Employees e ON e.EmployeeID = a.EmployeeID
        WHERE a.Status='Active' ORDER BY a.AdvanceDate DESC
    """)


def get_custom_field_defs(module):
    return db.query("""SELECT * FROM CustomFieldDefinitions WHERE ModuleName=? AND Active=1
                     ORDER BY DisplayOrder, FieldID""", (module,))


DASHBOARD_WIDGET_LABELS = {
    "stats": "Summary stat cards (sales, purchases, expenses, receivable/payable)",
    "targets": "Sales Target progress (MTD)",
    "low_stock": "Low stock alert",
    "over_stock": "Overstock alert",
    "maintenance_due": "Vehicle maintenance due",
    "docs_expiring": "Vehicle documents expiring",
    "salary_status": "Salary status this month",
    "active_advances": "Active employee advances",
    "gst_due": "GST filing due dates",
    "stock_returns": "Stock Return Variance (units & cost, monthly)",
}


DASHBOARD_WIDGET_ORDER_DEFAULT = list(DASHBOARD_WIDGET_LABELS.keys())


def get_dashboard_widgets():
    rows = db.query("SELECT * FROM DashboardWidgets ORDER BY DisplayOrder, WidgetKey")
    seen = {r["WidgetKey"] for r in rows}
    widgets = [dict(r) for r in rows]
    for i, k in enumerate(DASHBOARD_WIDGET_ORDER_DEFAULT):
        if k not in seen:
            widgets.append(dict(WidgetKey=k, Visible=1, DisplayOrder=1000 + i))
    widgets.sort(key=lambda w: w["DisplayOrder"])
    return widgets


LOCATION_TRACKED_ROLES = {"Staff", "Supervisor", "Manager"}


# Index matches Python's dt.weekday() (Monday=0) - used instead of dt.strftime('%a') so this
# never depends on the server process's OS locale (which could render day names in a different
# language and silently break the comparison against CompanySettings.LocationTrackingWorkingDays).
LOCATION_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


DEFAULT_LOCATION_WORKING_DAYS = "Tue,Wed,Thu,Fri,Sat,Sun"  # the app's original hardcoded rule


def _location_working_days(company=None):
    """{'Mon', 'Wed', ...} from CompanySettings.LocationTrackingWorkingDays, falling back to
    the app's original hardcoded rule (every day except Monday) if blank/not yet migrated."""
    company = company or get_company_settings()
    raw = company["LocationTrackingWorkingDays"] or DEFAULT_LOCATION_WORKING_DAYS
    return {d.strip() for d in raw.split(",") if d.strip()}


def target_month_bounds(year, month):
    days_in_month = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, days_in_month)
    return start, end, days_in_month


def target_days_elapsed(year, month):
    """How many days of the given month count as 'to date' for MTD/day-wise
    trend purposes: the full month if it's entirely in the past, 0 if it's
    entirely in the future, otherwise today's day-of-month."""
    start, end, days_in_month = target_month_bounds(year, month)
    today = date.today()
    if today < start:
        return 0
    if today > end:
        return days_in_month
    return today.day


def unlinked_sale_rows(employee_ids, date_from, date_to, product_id=None):
    """Completed Sales attributed to these employees whose quantity was NOT credited to a Stock
    Issue line (no open issue that day, issue oversold, or a late sale after reconciliation) -
    those units never reach StockIssueLines.QtySold, so Stock-Issue-only target actuals missed
    them entirely. Returns one dict per (sale, product) with only the un-credited share
    (qty/expected/discount/collected/date), so nothing is double-counted with the issue figures."""
    ids = list(employee_ids)
    if not ids:
        return []
    sql = f"""SELECT s.SaleDate, s.AmountReceived, s.TaxableAmount, SUM(sl.Qty) q,
                 SUM(sl.Qty*sl.UnitPrice - COALESCE(sl.DiscountAmount,0)) exp,
                 SUM(COALESCE(sl.DiscountAmount,0)) disc,
                 COALESCE((SELECT SUM(ssl.QtyApplied) FROM SaleStockIssueLinks ssl
                           JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                           WHERE ssl.SaleID = s.SaleID AND sil.ProductID = sl.ProductID), 0) linked
              FROM Sales s JOIN SalesLines sl ON sl.SaleID = s.SaleID
              WHERE s.EmployeeID IN ({','.join('?' * len(ids))}) AND s.Status='Completed'
                AND s.SaleDate BETWEEN ? AND ?{' AND sl.ProductID=?' if product_id else ''}
              GROUP BY s.SaleID, sl.ProductID"""
    params = tuple(ids) + (date_from, date_to) + ((product_id,) if product_id else ())
    out = []
    for r in db.query(sql, params):
        q = r["q"] or 0
        frac = max(q - r["linked"], 0) / q if q else 0
        if frac <= 0:
            continue
        exp = (r["exp"] or 0) * frac
        out.append({"date": r["SaleDate"], "qty": q * frac, "expected": exp, "discount": (r["disc"] or 0) * frac,
                    "collected": (r["AmountReceived"] or 0) * exp / r["TaxableAmount"] if r["TaxableAmount"] else 0})
    return out


def compute_target_actuals(employee_id, product_id, date_from, date_to):
    """Actuals for one Employee (+ optional Product) over a date range, all
    computed live from Stock Issues so a target is never stale. For a
    whole-employee target (product_id is None), Expected/Collected come
    straight from the Stock Issue header. For a product-level target,
    Expected is summed at line level (always exact); Collected is
    allocated proportionally per issue (each issue's actual cash split
    across its lines by each line's share of that issue's line-level
    expected amount) since CashCollected is only recorded per issue, not
    per product line."""
    if product_id:
        line_rows = db.query("""SELECT sil.QtySold, sil.UnitPrice, sil.DiscountAmount, sil.IssueID,
                              si.CashCollected, si.ExpectedAmount
                              FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                              WHERE si.EmployeeID=? AND sil.ProductID=? AND si.IssueDate BETWEEN ? AND ?""",
                              (employee_id, product_id, date_from, date_to))
        qty_sold = sum(r["QtySold"] or 0 for r in line_rows)
        discount = sum(r["DiscountAmount"] or 0 for r in line_rows)
        qty_no_discount = sum((r["QtySold"] or 0) for r in line_rows if not (r["DiscountAmount"] or 0))
        line_expected_by_issue = {}
        cash_by_issue = {}
        for r in line_rows:
            le = (r["QtySold"] or 0) * (r["UnitPrice"] or 0) - (r["DiscountAmount"] or 0)
            line_expected_by_issue[r["IssueID"]] = line_expected_by_issue.get(r["IssueID"], 0) + le
            cash_by_issue[r["IssueID"]] = (r["CashCollected"] or 0, r["ExpectedAmount"] or 0)
        expected = sum(line_expected_by_issue.values())
        collected = 0.0
        for iid, line_exp in line_expected_by_issue.items():
            cash, issue_exp = cash_by_issue[iid]
            collected += cash * (line_exp / issue_exp) if issue_exp else 0
    else:
        issue_rows = db.query("""SELECT ExpectedAmount, CashCollected FROM StockIssues
                               WHERE EmployeeID=? AND IssueDate BETWEEN ? AND ?""", (employee_id, date_from, date_to))
        expected = sum(r["ExpectedAmount"] or 0 for r in issue_rows)
        collected = sum(r["CashCollected"] or 0 for r in issue_rows)
        line_rows = db.query("""SELECT sil.QtySold, sil.DiscountAmount FROM StockIssueLines sil
                              JOIN StockIssues si ON si.IssueID = sil.IssueID
                              WHERE si.EmployeeID=? AND si.IssueDate BETWEEN ? AND ?""", (employee_id, date_from, date_to))
        qty_sold = sum(r["QtySold"] or 0 for r in line_rows)
        discount = sum(r["DiscountAmount"] or 0 for r in line_rows)
        qty_no_discount = sum((r["QtySold"] or 0) for r in line_rows if not (r["DiscountAmount"] or 0))
    for r in unlinked_sale_rows([employee_id], date_from, date_to, product_id):
        qty_sold += r["qty"]
        expected += r["expected"]
        collected += r["collected"]
        discount += r["discount"]
        if not r["discount"]:
            qty_no_discount += r["qty"]
    return {"qty_sold": round(qty_sold, 2), "expected": round(expected, 2), "collected": round(collected, 2),
            "discount": round(discount, 2), "qty_no_discount": round(qty_no_discount, 2)}


def pct(actual, target):
    if not target:
        return None
    return round(actual / target * 100, 1)


def get_target_buckets(target_id):
    """A Target's over-achievement incentive buckets, sorted highest threshold first -
    the order compute_incentive() needs to find the first (highest) threshold met."""
    return db.query("""SELECT * FROM TargetIncentiveBuckets WHERE TargetID=?
                     ORDER BY AchievementPct DESC""", (target_id,))


def compute_incentive(base_amount, buckets, achievement_pct):
    """Base Incentive Rs x the multiplier of the highest achievement-%% bucket threshold
    that's been met. Reaching no bucket at all (achievement below every threshold, or no
    buckets configured) means no incentive: multiplier 0, amount 0. Returns
    (multiplier_or_None, incentive_amount)."""
    if not buckets or achievement_pct is None:
        return None, 0.0
    for b in buckets:
        if achievement_pct >= b["AchievementPct"]:
            return b["Multiplier"], round(base_amount * b["Multiplier"], 2)
    return None, 0.0


def build_target_view(t, days_elapsed, days_in_month):
    """Attach full-month and MTD actuals + progress percentages to a Target row (as dict)."""
    start, end, _ = target_month_bounds(t["TargetYear"], t["TargetMonth"])
    mtd_end = start + timedelta(days=max(days_elapsed, 1) - 1) if days_elapsed else start
    full = compute_target_actuals(t["EmployeeID"], t["ProductID"], start.isoformat(), end.isoformat())
    mtd = compute_target_actuals(t["EmployeeID"], t["ProductID"], start.isoformat(), mtd_end.isoformat()) if days_elapsed else \
        {"qty_sold": 0, "expected": 0, "collected": 0, "discount": 0, "qty_no_discount": 0}
    prorated_qty = round(t["QtySoldTarget"] * days_elapsed / days_in_month, 2) if days_in_month else 0
    prorated_value = round(t["SalesValueTarget"] * days_elapsed / days_in_month, 2) if days_in_month else 0
    rate_cap_effective_full = round(t["DiscountCapRatePerUnit"] * full["qty_sold"], 2)
    rate_cap_effective_mtd = round(t["DiscountCapRatePerUnit"] * mtd["qty_sold"], 2)
    view = dict(t)
    view["full"] = full
    view["mtd"] = mtd
    view["prorated_qty"] = prorated_qty
    view["prorated_value"] = prorated_value
    view["qty_pct_full"] = pct(full["qty_sold"], t["QtySoldTarget"])
    view["qty_pct_mtd"] = pct(mtd["qty_sold"], prorated_qty)
    view["value_pct_full_expected"] = pct(full["expected"], t["SalesValueTarget"])
    view["value_pct_full_collected"] = pct(full["collected"], t["SalesValueTarget"])
    view["value_pct_mtd_expected"] = pct(mtd["expected"], prorated_value)
    view["discount_fixed_pct_full"] = pct(full["discount"], t["DiscountCapAmount"])
    view["discount_fixed_pct_mtd"] = pct(mtd["discount"], t["DiscountCapAmount"])
    view["discount_rate_cap_full"] = rate_cap_effective_full
    view["discount_rate_cap_mtd"] = rate_cap_effective_mtd
    view["discount_rate_pct_full"] = pct(full["discount"], rate_cap_effective_full)
    view["discount_rate_pct_mtd"] = pct(mtd["discount"], rate_cap_effective_mtd)
    view["no_discount_pct_full"] = round(full["qty_no_discount"] / full["qty_sold"] * 100, 1) if full["qty_sold"] else None
    buckets = get_target_buckets(t["TargetID"])
    view["buckets"] = buckets
    view["incentive_multiplier_full"], view["incentive_amount_full"] = compute_incentive(
        t["BaseIncentiveAmount"], buckets, view["qty_pct_full"])
    view["incentive_multiplier_mtd"], view["incentive_amount_mtd"] = compute_incentive(
        t["BaseIncentiveAmount"], buckets, view["qty_pct_mtd"])
    return view


def get_company_target_summary(year=None, month=None):
    """Whole-employee (all-products) targets combined across every
    salesperson that has one set for the given month (defaults to the
    current month) - used by both the Dashboard and Reports hub cards.
    Returns None if no such target exists yet for that month."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    days_elapsed = target_days_elapsed(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    rows = db.query("""SELECT * FROM Targets WHERE ProductID IS NULL
                     AND TargetYear=? AND TargetMonth=?""", (year, month))
    if not rows:
        return None
    views = [build_target_view(t, days_elapsed, days_in_month) for t in rows]
    qty_target = sum(t["QtySoldTarget"] for t in views)
    qty_prorated = sum(t["prorated_qty"] for t in views)
    qty_actual = sum(t["mtd"]["qty_sold"] for t in views)
    value_target = sum(t["SalesValueTarget"] for t in views)
    value_prorated = sum(t["prorated_value"] for t in views)
    expected_actual = sum(t["mtd"]["expected"] for t in views)
    discount_cap = sum(t["DiscountCapAmount"] for t in views)
    discount_actual = sum(t["mtd"]["discount"] for t in views)
    incentive_mtd = sum(t["incentive_amount_mtd"] for t in views)
    return {
        "employee_count": len(views), "qty_target": qty_target, "qty_prorated": qty_prorated,
        "qty_actual": qty_actual, "qty_pct": pct(qty_actual, qty_prorated),
        "value_target": value_target, "value_prorated": value_prorated, "expected_actual": expected_actual,
        "value_pct": pct(expected_actual, value_prorated),
        "discount_cap": discount_cap, "discount_actual": discount_actual,
        "discount_pct": pct(discount_actual, discount_cap),
        "incentive_mtd": incentive_mtd,
    }


def get_stock_returns_summary(year=None, month=None):
    """Per-salesperson Stock Return variance for the given month (defaults to the current
    month): positive UnitsVariance/ValueVariance = stock short/missing that month (less came
    back - sold or returned - than was issued); negative = more was accounted for than was
    issued (unusual - normally a data-entry slip, since you can't sell or return more than
    went out). Valued at Products.CostPrice (what the missing/excess stock cost the company
    to acquire, not its selling price). Only Reconciled issues are counted - an issue still
    "Issued" has no final Qty Returned/Qty Free yet, so there's nothing settled to measure."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    month_start = date(year, month, 1).isoformat()
    month_end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    rows = db.query("""
        SELECT e.EmployeeID, e.EmployeeName,
               COALESCE(SUM(sil.QtyIssued - COALESCE(sil.QtySold,0) - COALESCE(sil.QtyReturned,0)
                            - COALESCE(sil.QtyFree,0)), 0) AS UnitsVariance,
               COALESCE(SUM((sil.QtyIssued - COALESCE(sil.QtySold,0) - COALESCE(sil.QtyReturned,0)
                            - COALESCE(sil.QtyFree,0)) * COALESCE(pr.CostPrice,0)), 0) AS ValueVariance
        FROM StockIssues si
        JOIN Employees e ON e.EmployeeID = si.EmployeeID
        JOIN StockIssueLines sil ON sil.IssueID = si.IssueID
        JOIN Products pr ON pr.ProductID = sil.ProductID
        WHERE si.Status='Reconciled' AND si.IssueDate BETWEEN ? AND ?
        GROUP BY e.EmployeeID, e.EmployeeName
        ORDER BY ABS(ValueVariance) DESC, e.EmployeeName
    """, (month_start, month_end))
    rows = [dict(r) for r in rows]
    for r in rows:
        r["UnitsVariance"] = round(r["UnitsVariance"], 2)
        r["ValueVariance"] = round(r["ValueVariance"], 2)
    totals = {
        "units": round(sum(r["UnitsVariance"] for r in rows), 2),
        "value": round(sum(r["ValueVariance"] for r in rows), 2),
        "short_count": sum(1 for r in rows if r["UnitsVariance"] > 0.001),
        "employee_count": len(rows),
    }
    return {"rows": rows, "totals": totals}


def get_customer_due_info(customer_id):
    """Outstanding balance + oldest unpaid invoice age for one customer -
    deliberately the same definition the Accounts Receivable report uses
    (TaxableAmount - AmountReceived, summed across every non-Cancelled sale;
    PaymentDueDate falling back to SaleDate for aging), so a number shown
    here always matches what that report would show for the same customer.
    Used by the New/Edit Sale "customer has dues" warning and credit block."""
    rows = db.query("""SELECT s.PaymentDueDate, s.SaleDate, (s.TaxableAmount - s.AmountReceived) AS Due
                     FROM Sales s WHERE s.CustomerID=? AND s.Status <> 'Cancelled'
                       AND (s.TaxableAmount - s.AmountReceived) > 0.005""", (customer_id,))
    due = round(sum(r["Due"] for r in rows), 2)
    oldest_due_date = None
    for r in rows:
        d = r["PaymentDueDate"] or r["SaleDate"]
        if d and (oldest_due_date is None or d < oldest_due_date):
            oldest_due_date = d
    days_overdue = (date.today() - date.fromisoformat(oldest_due_date)).days if oldest_due_date else 0
    return {"due": due, "oldest_due_date": oldest_due_date, "days_overdue": days_overdue}


UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")


# 2-digit GST state/UT codes as per the GSTIN structure (Indian states + UTs)
INDIAN_STATES = [
    ("01", "Jammu and Kashmir"), ("02", "Himachal Pradesh"), ("03", "Punjab"),
    ("04", "Chandigarh"), ("05", "Uttarakhand"), ("06", "Haryana"), ("07", "Delhi"),
    ("08", "Rajasthan"), ("09", "Uttar Pradesh"), ("10", "Bihar"), ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"), ("13", "Nagaland"), ("14", "Manipur"), ("15", "Mizoram"),
    ("16", "Tripura"), ("17", "Meghalaya"), ("18", "Assam"), ("19", "West Bengal"),
    ("20", "Jharkhand"), ("21", "Odisha"), ("22", "Chhattisgarh"), ("23", "Madhya Pradesh"),
    ("24", "Gujarat"), ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"), ("28", "Andhra Pradesh (Old)"), ("29", "Karnataka"),
    ("30", "Goa"), ("31", "Lakshadweep"), ("32", "Kerala"), ("33", "Tamil Nadu"),
    ("34", "Puducherry"), ("35", "Andaman and Nicobar Islands"), ("36", "Telangana"),
    ("37", "Andhra Pradesh"), ("38", "Ladakh"),
]


STATE_NAME_BY_CODE = dict(INDIAN_STATES)


def get_financial_year_label(d):
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def next_invoice_number():
    """Allocates the next sequential GST invoice number for the current
    financial year, formatted <Prefix>/<FY>/<0001>. Resets to 0001 when a
    new financial year (1 Apr) begins.

    Uses db.transaction() (reentrant) rather than opening its own separate
    connection: create_sale() calls this from inside its own transaction()
    block, which by then may already hold a write lock on that connection
    (e.g. when called from stock_issue_reconcile(), well after several
    other UPDATEs) - a second, independent connection trying to write here
    would deadlock against that lock under SQLite's single-writer model
    (the same thread waiting on itself, since nothing else will ever commit
    to release it). Reentrant means a standalone call (no outer transaction
    active) still gets its own short-lived one, same as before."""
    fy = get_financial_year_label(date.today())
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT NextInvoiceSeq, InvoiceSeqFY, InvoicePrefix FROM CompanySettings WHERE SettingsID=1").fetchone()
        seq = row["NextInvoiceSeq"] if row["InvoiceSeqFY"] == fy else 1
        prefix = row["InvoicePrefix"] or "INV"
        invoice_no = f"{prefix}/{fy}/{seq:04d}"
        conn.execute("UPDATE CompanySettings SET NextInvoiceSeq=?, InvoiceSeqFY=? WHERE SettingsID=1", (seq + 1, fy))
    return invoice_no


def next_stock_issue_number():
    """Allocates the next sequential Stock Issue number for the current
    financial year, formatted <Prefix>/<FY>/<0001> - same pattern and same
    reentrant-transaction reasoning as next_invoice_number() (stock_issue_form()
    doesn't currently call this from inside another transaction, but keeping
    the same safe pattern here costs nothing and avoids a future surprise if
    that ever changes)."""
    fy = get_financial_year_label(date.today())
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT NextStockIssueSeq, StockIssueSeqFY, StockIssuePrefix FROM CompanySettings WHERE SettingsID=1").fetchone()
        seq = row["NextStockIssueSeq"] if row["StockIssueSeqFY"] == fy else 1
        prefix = row["StockIssuePrefix"] or "SI"
        issue_number = f"{prefix}/{fy}/{seq:04d}"
        conn.execute("UPDATE CompanySettings SET NextStockIssueSeq=?, StockIssueSeqFY=? WHERE SettingsID=1", (seq + 1, fy))
    return issue_number


def next_customer_account_number():
    """Allocates the next sequential 'HHGnnnn' account number for a brand
    new Customer - a permanent, never-reused identifier, unlike the row's
    CustomerID (stable but not meant to be handed out as a business
    reference) or its position in a sorted/paginated list. Never resets
    (unlike next_invoice_number()'s per-financial-year counter).

    Uses db.transaction() (reentrant) for the same reason next_invoice_number()
    does: resolve_sale_customer() can create a brand-new Customer from inside
    create_sale()'s own transaction, which may already hold a write lock on
    that connection - a second, independent connection trying to write here
    would deadlock against that lock under SQLite's single-writer model."""
    with db.transaction() as conn:
        seq = conn.execute("SELECT NextCustomerAccountSeq FROM CompanySettings WHERE SettingsID=1").fetchone()[0]
        account_number = f"HHG{seq:04d}"
        conn.execute("UPDATE CompanySettings SET NextCustomerAccountSeq=? WHERE SettingsID=1", (seq + 1,))
    return account_number


def compute_line_gst(taxable_value, gst_rate, is_interstate):
    """Splits GST into CGST+SGST (intra-state) or IGST (inter-state)."""
    if is_interstate:
        igst_rate = gst_rate
        igst_amt = round(taxable_value * igst_rate / 100, 2)
        return dict(cgst_rate=0, cgst_amt=0, sgst_rate=0, sgst_amt=0, igst_rate=igst_rate, igst_amt=igst_amt)
    else:
        half = gst_rate / 2
        cgst_amt = round(taxable_value * half / 100, 2)
        sgst_amt = round(taxable_value * half / 100, 2)
        return dict(cgst_rate=half, cgst_amt=cgst_amt, sgst_rate=half, sgst_amt=sgst_amt, igst_rate=0, igst_amt=0)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]


_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digit_words(n):
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _three_digit_words(n):
    if n >= 100:
        return _ONES[n // 100] + " Hundred" + (" " + _two_digit_words(n % 100) if n % 100 else "")
    return _two_digit_words(n)


def amount_in_words(amount):
    """Converts a rupee amount to words using the Indian numbering system
    (Crore/Lakh/Thousand), for the statutory 'Amount in Words' line."""
    num = int(round(amount))
    if num == 0:
        return "Zero Rupees Only"
    parts = []
    crore, num = divmod(num, 10000000)
    lakh, num = divmod(num, 100000)
    thousand, num = divmod(num, 1000)
    hundred = num
    if crore:
        parts.append(_three_digit_words(crore) + " Crore")
    if lakh:
        parts.append(_three_digit_words(lakh) + " Lakh")
    if thousand:
        parts.append(_three_digit_words(thousand) + " Thousand")
    if hundred:
        parts.append(_three_digit_words(hundred))
    return " ".join(parts) + " Rupees Only"


def custom_field_attachment_module(field_id):
    """The synthetic Attachments.ModuleName used for an attachment-type custom field."""
    return f"CustomField:{field_id}"


def get_attachments(module, record_id):
    return db.query("""SELECT * FROM Attachments WHERE ModuleName=? AND RecordID=?
                     ORDER BY UploadedAt DESC, AttachmentID DESC""", (module, record_id))


def get_custom_values(module, record_id):
    rows = db.query("""SELECT d.FieldKey, v.ValueText FROM CustomFieldValues v
                     JOIN CustomFieldDefinitions d ON d.FieldID = v.FieldID
                     WHERE d.ModuleName=? AND v.RecordID=?""", (module, record_id))
    return {r["FieldKey"]: r["ValueText"] for r in rows}


def save_custom_fields(module, record_id, form):
    for d in get_custom_field_defs(module):
        if d["FieldType"] == "attachment":
            continue  # files for these are uploaded/stored separately via the Attachments table
        field_name = f"custom_{d['FieldKey']}"
        if d["FieldType"] == "checkbox":
            value = "1" if form.get(field_name) else "0"
        else:
            value = form.get(field_name, "")
        existing = db.query("SELECT ValueID FROM CustomFieldValues WHERE FieldID=? AND RecordID=?",
                            (d["FieldID"], record_id), one=True)
        if existing:
            db.execute("UPDATE CustomFieldValues SET ValueText=? WHERE ValueID=?", (value, existing["ValueID"]))
        else:
            db.execute("INSERT INTO CustomFieldValues (FieldID, RecordID, ValueText) VALUES (?,?,?)",
                       (d["FieldID"], record_id, value))


def get_custom_attachments(module, record_id):
    """dict FieldID -> list of Attachments rows, for this module's active
    attachment-type custom fields on one record. Empty dict for a record
    that doesn't exist yet (nothing to attach to until it's saved)."""
    if not record_id:
        return {}
    out = {}
    for d in get_custom_field_defs(module):
        if d["FieldType"] == "attachment":
            out[d["FieldID"]] = get_attachments(custom_field_attachment_module(d["FieldID"]), record_id)
    return out


# =======================================================================
# DASHBOARD WIDGET CUSTOMIZATION
# =======================================================================

# =======================================================================
# LIST VIEW CUSTOMIZABLE COLUMNS
# =======================================================================


# Base (built-in) columns available per module — key must match the
# data-col attribute used in that module's list template.
MODULE_COLUMNS = {
    "Product": [("product_name", "Product"), ("sku", "SKU"), ("category", "Category"),
                ("stock", "Stock"), ("min_stock", "Min"), ("max_stock", "Max"),
                ("cost_price", "Cost"), ("selling_price", "Selling"), ("scheme", "Scheme"),
                ("incentive", "Incentive"), ("supplier", "Supplier"), ("status", "Status")],
    "Supplier": [("name", "Name"), ("contact", "Contact"), ("phone", "Phone"),
                 ("email", "Email"), ("gstin", "GSTIN"), ("status", "Status")],
    "Customer": [("account_number", "Account #"), ("name", "Name"), ("contact", "Contact"), ("phone", "Phone"),
                 ("zone", "Zone"), ("parent_account", "Parent Account"),
                 ("credit_limit", "Credit Limit"), ("credit_days", "Credit Days"), ("status", "Status")],
    "Purchase": [("po_number", "PO #"), ("supplier", "Supplier"), ("date", "Date"),
                 ("invoice_number", "Invoice #"), ("status", "Status"), ("payment", "Payment"),
                 ("total", "Total")],
    "Sale": [("invoice_number", "Invoice #"), ("customer", "Customer"), ("date", "Date"),
             ("status", "Status"), ("payment", "Payment"), ("due_date", "Due Date"), ("total", "Taxable Amt"),
             ("balance", "Balance"), ("salesperson", "Salesperson"), ("cash", "Cash ₹"), ("bank", "Bank ₹")],
    "Expense": [("date", "Date"), ("category", "Category"), ("vehicle", "Vehicle"),
                ("paid_to", "Paid To"), ("mode", "Mode"), ("amount", "Amount")],
    "Vehicle": [("reg_number", "Reg. No"), ("type", "Type"), ("make_model", "Make/Model"),
                ("odometer", "Odometer"), ("purchase_price", "Purchase Price"),
                ("insurance", "Insurance"), ("permit", "Permit"),
                ("puc", "PUC"), ("fitness", "Fitness"), ("status", "Status")],
    "Maintenance": [("vehicle", "Vehicle"), ("service_type", "Service Type"), ("date", "Date"),
                     ("odometer", "Odometer"), ("next_due_date", "Next Due Date"), ("cost", "Cost"),
                     ("service_center", "Service Center")],
    "Employee": [("name", "Name"), ("designation", "Designation"), ("phone", "Phone"),
                 ("join_date", "Join Date"), ("monthly_salary", "Monthly Salary"), ("status", "Status")],
    "SalaryPayment": [("employee", "Employee"), ("designation", "Designation"), ("basic", "Basic"),
                       ("advance_deducted", "Advance Deducted"), ("net_payable", "Net Payable"),
                       ("status", "Status")],
    "Advance": [("employee", "Employee"), ("date", "Date"), ("amount", "Amount"), ("reason", "Reason"),
                ("repay_months", "Repay (months)"), ("monthly_deduction", "Monthly Deduction"),
                ("balance", "Balance"), ("status", "Status")],
    "SalesLive": [("salesperson", "Salesperson"), ("status", "Status"), ("qty_issued", "Qty Issued"),
                  ("qty_sold", "Qty Sold"), ("qty_returned", "Qty Returned"), ("qty_free", "Qty Free"),
                  ("unaccounted", "Unaccounted"), ("sales_count", "Sales"), ("cash", "Cash ₹"),
                  ("bank", "Bank ₹"), ("discount", "Discount ₹"), ("expected", "Expected ₹"),
                  ("collected", "Collected ₹"), ("discrepancy", "Discrepancy ₹"), ("month_pace", "Month Pace"),
                  ("top_products", "Top Products Today")],
    "StockIssue": [("issue_number", "Issue #"), ("date", "Date"), ("salesperson", "Salesperson"), ("status", "Status"),
                   ("month_target", "Month Target"), ("expected", "Expected"), ("collected", "Collected"),
                   ("discrepancy", "Discrepancy"), ("qty_sold", "Qty Sold"), ("discount", "Discount"),
                   ("scheme", "Scheme Amount"), ("unaccounted", "Unaccounted")],
}


# Admin-customizable field layout for the New/Edit Sale form (visibility + placement),
# stored in FormFieldLayout. "SaleFormHeader" fields can be freely reordered/hidden via
# CSS flexbox order on the form (see sale_form.html); a field marked required in the
# form's HTML stays required even if hidden, so don't hide Customer/Sale Date/Place of
# Supply/Line items without also removing "required" from the template - hiding those is
# not offered as an option below for that reason. "SaleFormLine" columns control which
# product-line-item table columns are shown (order is fixed - reordering table columns
# safely needs JS column-swapping, not just CSS, so that's not offered yet).
FORM_FIELD_MODULES = {
    "SaleFormHeader": [
        ("invoice_number", "Invoice Number"),
        ("customer", "Customer (search/select)"),
        ("sale_date", "Sale Date"),
        ("status", "Status"),
        ("place_of_supply", "Place of Supply (State)"),
        ("customer_details", "Customer Details (Name/Address/Phone/Zone)"),
        ("salesperson_warning", "Salesperson-not-linked warning"),
        ("payment_due_date", "Payment Due Date"),
        ("payment_status", "Payment Status"),
        ("cash_bank_amount", "Cash / Bank Amount Received"),
        ("reverse_charge", "Reverse Charge"),
        ("salesperson", "Salesperson"),
        ("notes", "Notes"),
    ],
    "SaleFormLine": [
        ("product", "Product"),
        ("qty", "Qty"),
        ("unit_price", "Unit Price"),
        ("discount_amount", "Discount ₹"),
    ],
}


# Fields an Admin can't hide, because the form can't function or validate without them.
FORM_FIELD_ALWAYS_VISIBLE = {
    "SaleFormHeader": {"customer", "sale_date", "place_of_supply"},
    "SaleFormLine": {"product", "qty", "unit_price"},
}


def get_effective_form_fields(module):
    """Same idea as get_effective_columns(), but for FormFieldLayout (form fields rather
    than list-view columns). Returns the ordered list of field dicts with saved
    visibility/order applied; fields in FORM_FIELD_ALWAYS_VISIBLE are forced visible
    regardless of what's stored."""
    all_fields = [dict(key=k, label=lbl) for k, lbl in FORM_FIELD_MODULES.get(module, [])]
    prefs = {r["FieldKey"]: r for r in db.query(
        "SELECT * FROM FormFieldLayout WHERE ModuleName=?", (module,))}
    always_visible = FORM_FIELD_ALWAYS_VISIBLE.get(module, set())
    for i, f in enumerate(all_fields):
        pref = prefs.get(f["key"])
        if pref:
            f["visible"] = bool(pref["Visible"])
            f["order"] = pref["DisplayOrder"]
        else:
            f["visible"] = True
            f["order"] = i
        if f["key"] in always_visible:
            f["visible"] = True
    all_fields.sort(key=lambda f: f["order"])
    return all_fields


def get_effective_columns(module):
    """Returns the ordered list of column dicts for a module's list view,
    with saved visibility/order applied. Includes hidden columns too
    (callers filter by 'visible' as needed) so the customize page can
    show everything. (Custom fields have their own place on the record's
    form/detail view — this only covers the built-in list columns.)"""
    all_cols = [dict(key=k, label=lbl, kind="base") for k, lbl in MODULE_COLUMNS.get(module, [])]

    prefs = {r["ColumnKey"]: r for r in db.query(
        "SELECT * FROM ListViewColumns WHERE ModuleName=?", (module,))}

    for i, col in enumerate(all_cols):
        pref = prefs.get(col["key"])
        if pref:
            col["visible"] = bool(pref["Visible"])
            col["order"] = pref["DisplayOrder"]
        else:
            col["visible"] = True
            col["order"] = i

    all_cols.sort(key=lambda c: c["order"])
    return all_cols


def find_open_stock_issue(employee_id, sale_date):
    """The Stock Issue (if any) for this salesperson on this exact date that
    hasn't been reconciled yet - the only kind a directly-entered Sale is
    allowed to live-credit against (bumping its QtySold/SaleStockIssueLinks).
    A late sale against an ALREADY-Reconciled issue is handled separately in
    create_sale() (see the `reconciled_issue` capacity check there) - its
    stock still came from that day's van stock and shouldn't post a second
    warehouse deduction if the reconciled issue has any unaccounted-for
    capacity left, but the reconciled issue's own StockIssueLines figures
    are deliberately left untouched/frozen rather than live-credited."""
    if not employee_id:
        return None
    return db.query("""SELECT * FROM StockIssues WHERE EmployeeID=? AND IssueDate=? AND Status='Issued'
                     ORDER BY IssueID DESC LIMIT 1""", (employee_id, sale_date), one=True)


def auto_create_stock_issue_for_sale(employee_id, sale_date, line_data):
    """Backfills a Stock Issue for a salesperson's Sale when they had none
    open yet for that date — reuses stock_issue_post_line() (the same
    helper "Issue Stock"/"Add More Products" use) so the Issue transaction
    posting and StockIssueLines bookkeeping stay identical to a manually
    created issue. Flagged ReviewStatus='Pending' so a Manager/Admin has to
    look it over before it can be reconciled — the sale itself has already
    gone through by the time this runs, so this never blocks the sale."""
    issue_id = db.execute("""INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status, ReviewStatus, Notes)
                VALUES (?,?,?,?,?,?)""",
               (next_stock_issue_number(), employee_id, sale_date, "Issued", "Pending",
                "Auto-created from a Sales-tab entry — pending Manager/Admin review"))
    log_stock_issue_change(issue_id, "Created", None, None,
                            "Auto-created from a Sales-tab entry (pending review)")
    for prod_id, qty, price, *_rest in line_data:
        stock_issue_post_line(issue_id, prod_id, qty, price, sale_date,
                               "Auto-issued to cover a Sales-tab entry (pending review)")
    return issue_id


def expand_pending_stock_issue_for_product(issue, product_id, qty, price, sale_date):
    """A 'Pending' (auto-created) Stock Issue can be silently topped up with
    a product it doesn't yet have a line for, if a later Sale that same day
    sells something new — it's still awaiting its first human review, so
    there's no manually-entered state to clobber. A genuine 'Reviewed'
    issue (manually created, or already approved) is never touched this
    way — this function is only called for ReviewStatus=='Pending' ones."""
    stock_issue_post_line(issue["IssueID"], product_id, qty, price, sale_date,
                           "Auto-issued to cover a Sales-tab entry (pending review)")


def reverse_sale_stock_issue_links(sale_id):
    """Undoes whatever a prior save of this Sale credited against any Stock
    Issue lines' Qty Sold (via SaleStockIssueLinks), and removes those link
    rows - the first step whenever a Sale is being edited, before its
    quantities/salesperson/date are re-evaluated and (maybe) re-credited
    from scratch. Floors at 0 so this is safe even if the Stock Issue line
    was itself edited down in the meantime."""
    links = db.query("SELECT * FROM SaleStockIssueLinks WHERE SaleID=?", (sale_id,))
    for link in links:
        db.execute("""UPDATE StockIssueLines SET QtySold = MAX(COALESCE(QtySold, 0) - ?, 0),
                    DiscountAmount = MAX(COALESCE(DiscountAmount, 0) - ?, 0) WHERE LineID=?""",
                   (link["QtyApplied"], link["DiscountApplied"] or 0, link["StockIssueLineID"]))
    db.execute("DELETE FROM SaleStockIssueLinks WHERE SaleID=?", (sale_id,))


class SaleStockIssueLockedError(Exception):
    """Raised by create_sale() when editing a Sale would re-run its stock/credit posting
    logic against a salesperson's Stock Issue that's already Reconciled - see the guard at
    the top of create_sale()'s edit branch. Reconciled figures are meant to be a closed
    book; blindly re-deriving them from whatever the world looks like at edit time (rather
    than when the Sale was first entered) is exactly what silently corrupted a Stock
    Issue's numbers once already. Callers should show locked_sale_message()."""
    def __init__(self, issue_id):
        self.issue_id = issue_id
        super().__init__(f"Sale is entangled with reconciled Stock Issue #{issue_id}")


def locked_sale_message(issue_id, action="edit"):
    return (f"Nothing was saved. This sale is tied to Stock Issue #{issue_id}, which is already Reconciled, so its "
            f"lines, quantities, discounts, salesperson, date and status can't be changed or deleted directly. "
            f"To correct them, use 'Reopen & Correct' on Stock Issue #{issue_id} to adjust the reconciliation "
            f"figures. " + ("Only the Notes and Due Date can still be edited here." if action == "edit"
                           else "The sale itself stays as recorded."))


def locked_issue_for_sale(sale_id):
    """IssueID of the Reconciled Stock Issue this Sale is tied to, else None. Tied means it credited one
    of that issue's lines (SaleStockIssueLinks), or it's a Completed sale whose stored (salesperson, date)
    matches a Reconciled issue. Anything that would rewrite or remove such a sale must be blocked."""
    row = db.query("""SELECT si.IssueID FROM SaleStockIssueLinks ssl
                    JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                    JOIN StockIssues si ON si.IssueID = sil.IssueID
                    WHERE ssl.SaleID=? AND si.Status='Reconciled' LIMIT 1""", (sale_id,), one=True)
    if row:
        return row["IssueID"]
    sale = db.query("SELECT EmployeeID, SaleDate, Status FROM Sales WHERE SaleID=?", (sale_id,), one=True)
    if sale and sale["EmployeeID"] and sale["Status"] == "Completed":
        row = db.query("""SELECT IssueID FROM StockIssues WHERE EmployeeID=? AND IssueDate=? AND Status='Reconciled'
                        ORDER BY IssueID DESC LIMIT 1""", (sale["EmployeeID"], sale["SaleDate"]), one=True)
        return row["IssueID"] if row else None
    return None


def create_sale(customer_id, sale_date, status, payment_status, payment_due_date, amount_received, notes,
                 place_of_supply_code, lines, reverse_charge=False, invoice_no=None, sale_id=None,
                 post_inventory=True, employee_id=None, cash_amount=None, bank_amount=None,
                 auto_create_issue=False):
    """Creates a Sale + SalesLines with the GST breakup, or (when `sale_id`
    is passed) EDITS an existing one in place: its old SalesLines and the
    InventoryTransaction rows it originally created are removed first, then
    recreated from the new `lines`/`status` — mirrors create_purchase()'s
    edit path. `lines` is a list of (product_id, qty, unit_price) tuples.
    The invoice number is never changed on an edit (pass the existing one
    in via `invoice_no`) so numbering/any already-printed invoice stays
    consistent.

    `post_inventory=False` skips posting this Sale's own stock-deduction
    InventoryTransactions — used when the stock effect is already fully
    accounted for elsewhere (e.g. a Sale auto-created from a reconciled
    Stock Issue, whose Issue/Return-In/Free-Scheme transactions already
    cover the sold units; a second deduction here would double-count it).

    `employee_id`, when given, names the salesperson this sale is against.
    If that salesperson has an open (not-yet-reconciled) Stock Issue for
    `sale_date`, each line's quantity is first credited against that Stock
    Issue's matching product line's Qty Sold (up to however much of it is
    still unaccounted-for) instead of deducting warehouse stock directly —
    the stock was already deducted once when it was issued out, so crediting
    it here (rather than posting a second "Sale" deduction) avoids double-
    counting, and it makes the sale show up pre-filled on that issue's
    Reconcile screen. Only the portion of a line beyond what the day's Stock
    Issue can cover (no employee/issue, product not on it, or oversold past
    what's still unaccounted) falls back to a normal warehouse deduction.
    Exactly how much was credited where is recorded in SaleStockIssueLinks
    so a later edit of this same Sale can precisely reverse and redo it.

    `lines` is a list of (product_id, qty, unit_price) OR
    (product_id, qty, unit_price, discount_per_unit) tuples — the 4-tuple
    form's discount is a PER-UNIT Rs amount, multiplied by Qty to get the
    line's total discount (Qty x UnitPrice - Qty x discount_per_unit,
    floored at 0) before GST is calculated on it. The total (not the
    per-unit rate) is what's stored in SalesLines.DiscountAmount, since
    every report/aggregate elsewhere (Live Sales Monitor, Targets, Stock
    Issue schemes, etc.) sums that column expecting a per-line total.

    `cash_amount`/`bank_amount` record how AmountReceived was actually
    collected (physical cash vs. bank/UPI/card). Both default to None, in
    which case the whole of `amount_received` is recorded as cash — kept
    backward compatible for callers (bulk import, the auto-created
    reconciliation Sale) that don't split payment mode.

    `auto_create_issue=True` additionally means: if `employee_id` has no
    open Stock Issue for `sale_date` yet, create one automatically (via
    auto_create_stock_issue_for_sale()) from this sale's own lines, flagged
    'Pending Review', so the sale can still credit against it instead of
    deducting warehouse stock a second time. Only sale_form()'s direct
    salesperson-locked entry point passes this — every other caller leaves
    it False so it never surprises a manual/imported/reconciliation sale."""
    # The whole operation is one atomic transaction (see db.transaction()) -
    # this function makes a dozen-plus separate inserts/updates (Sale header,
    # lines, inventory postings, Stock Issue credits), and used to commit each
    # one independently; an exception partway through left a permanently
    # half-written Sale. Now either all of it lands, or none of it does.
    with db.transaction():
        company = get_company_settings()
        place_of_supply_name = STATE_NAME_BY_CODE.get(place_of_supply_code, "")
        is_interstate = 1 if (company["StateCode"] and place_of_supply_code != company["StateCode"]) else 0

        if invoice_no is None:
            invoice_no = next_invoice_number()

        taxable_total = cgst_total = sgst_total = igst_total = discount_total = 0.0
        line_data = []
        for line in lines:
            prod_id, qty, price = line[0], line[1], line[2]
            discount_per_unit = line[3] if len(line) > 3 else 0.0
            discount = round(discount_per_unit * qty, 2)  # per-line total, for storage/aggregation
            prod = db.query("SELECT HSNCode, GSTRate FROM Products WHERE ProductID=?", (prod_id,), one=True)
            hsn = (prod["HSNCode"] or "") if prod else ""
            gst_rate = (prod["GSTRate"] or 0) if prod else 0
            taxable_value = round(max(qty * price - discount, 0), 2)
            gst = compute_line_gst(taxable_value, gst_rate, is_interstate)
            taxable_total += taxable_value
            cgst_total += gst["cgst_amt"]
            sgst_total += gst["sgst_amt"]
            igst_total += gst["igst_amt"]
            discount_total += discount
            line_data.append((prod_id, qty, price, discount, taxable_value, hsn, gst_rate, gst))

        raw_total = taxable_total + cgst_total + sgst_total + igst_total
        grand_total = round(raw_total)
        round_off = round(grand_total - raw_total, 2)

        if cash_amount is None and bank_amount is None:
            cash_amount, bank_amount = amount_received, 0.0
        cash_amount = round(cash_amount or 0, 2)
        bank_amount = round(bank_amount or 0, 2)

        if sale_id is None:
            sale_id = db.execute("""INSERT INTO Sales (InvoiceNumber, CustomerID, SaleDate, Status, PaymentStatus,
                        PaymentDueDate, TotalAmount, AmountReceived, Notes, PlaceOfSupplyState, PlaceOfSupplyStateCode,
                        IsInterState, TaxableAmount, CGSTAmount, SGSTAmount, IGSTAmount, RoundOff, ReverseCharge, EmployeeID,
                        CashAmount, BankAmount, CreatedAt)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (invoice_no, customer_id, sale_date, status, payment_status,
                         payment_due_date or None, grand_total, amount_received, notes,
                         place_of_supply_name, place_of_supply_code, is_interstate,
                         round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                         round_off, 1 if reverse_charge else 0, employee_id, cash_amount, bank_amount,
                         _now_ist().strftime("%Y-%m-%d %H:%M:%S")))
        else:
            # Guardrail: block editing a Sale entangled with an already-Reconciled Stock Issue -
            # re-running the credit/covered/direct-deduction logic below against "whatever the Stock
            # Issue looks like right now" rather than when the Sale was first saved is exactly how
            # one Reconciled issue's Qty Sold got silently double-counted. Applies whatever the NEW
            # status is: cancelling or drafting a Completed sale also reverses its Stock Issue credit,
            # which would silently rewrite the closed issue too. Only skipped for post_inventory=False -
            # the internal calls from stock_issue_reconcile()'s auto-invoice and sale_reassign() pass
            # that and no employee_id, so they're naturally exempt.
            if post_inventory:
                locked_issue_id = None
                existing_sale_row = db.query("SELECT Status FROM Sales WHERE SaleID=?", (sale_id,), one=True)
                if existing_sale_row and existing_sale_row["Status"] == "Completed":
                    locked_issue_id = locked_issue_for_sale(sale_id)
                if not locked_issue_id and status == "Completed" and employee_id:
                    new_target = db.query("""SELECT IssueID FROM StockIssues WHERE EmployeeID=? AND IssueDate=?
                                           AND Status='Reconciled'""", (employee_id, sale_date), one=True)
                    locked_issue_id = new_target["IssueID"] if new_target else None
                if locked_issue_id:
                    raise SaleStockIssueLockedError(locked_issue_id)
            db.execute("""UPDATE Sales SET CustomerID=?, SaleDate=?, Status=?, PaymentStatus=?, PaymentDueDate=?,
                        TotalAmount=?, AmountReceived=?, Notes=?, PlaceOfSupplyState=?, PlaceOfSupplyStateCode=?,
                        IsInterState=?, TaxableAmount=?, CGSTAmount=?, SGSTAmount=?, IGSTAmount=?, RoundOff=?,
                        ReverseCharge=?, EmployeeID=?, CashAmount=?, BankAmount=? WHERE SaleID=?""",
                       (customer_id, sale_date, status, payment_status, payment_due_date or None,
                        grand_total, amount_received, notes, place_of_supply_name, place_of_supply_code, is_interstate,
                        round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                        round_off, 1 if reverse_charge else 0, employee_id, cash_amount, bank_amount, sale_id))
            db.execute("DELETE FROM SalesLines WHERE SaleID=?", (sale_id,))
            # Delete any DirectSaleReviews rows for this Sale's old warehouse-deduction
            # transactions first - PRAGMA foreign_keys=ON would otherwise block deleting
            # the InventoryTransactions rows they reference.
            db.execute("""DELETE FROM DirectSaleReviews WHERE TransactionID IN
                        (SELECT TransactionID FROM InventoryTransactions WHERE RefType='Sale' AND RefID=?)""", (sale_id,))
            db.execute("DELETE FROM InventoryTransactions WHERE RefType='Sale' AND RefID=?", (sale_id,))
            # Undo whatever this Sale previously credited toward any Stock Issue's Qty Sold,
            # before re-evaluating (below) what it should credit now - the employee, date, or
            # quantities may all have changed since the last save.
            reverse_sale_stock_issue_links(sale_id)

        open_issue = find_open_stock_issue(employee_id, sale_date) if (status == "Completed" and post_inventory) else None
        # A salesperson's Stock Issue for this exact date that's ALREADY been Reconciled, checked
        # separately from open_issue above (which only matches Status='Issued') - used below purely
        # to decide whether a late-entered or later-edited Sale for that employee/date should still
        # post its own warehouse deduction. Its stock genuinely came from that same day's van stock,
        # not a fresh warehouse pickup, so as long as the reconciled issue's own line still has
        # capacity left unaccounted-for (QtyIssued minus whatever's already been returned, given
        # free, or truly sold that day - see true_product_qty_sold()), this Sale should fill that
        # gap instead of deducting warehouse stock a second time. StockIssueLines/SaleStockIssueLinks
        # are deliberately left untouched for a Reconciled issue (its own figures stay frozen/
        # historical, exactly as an Admin reconciled them) - only the ledger deduction decision uses
        # this, recomputed fresh from true_product_qty_sold every time so multiple late sales against
        # the same reconciled day correctly divide up whatever capacity remains between them.
        reconciled_issue = None
        if employee_id and status == "Completed" and post_inventory:
            reconciled_issue = db.query("""SELECT * FROM StockIssues WHERE EmployeeID=? AND IssueDate=?
                                         AND Status='Reconciled' ORDER BY IssueID DESC LIMIT 1""",
                                        (employee_id, sale_date), one=True)
        if open_issue is None and employee_id and auto_create_issue and status == "Completed" and post_inventory and line_data:
            issue_id = auto_create_stock_issue_for_sale(employee_id, sale_date, line_data)
            open_issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
        elif open_issue is not None and open_issue["ReviewStatus"] == "Pending" and employee_id and status == "Completed" and post_inventory:
            # A Pending (auto-created, not-yet-reviewed) issue can be silently topped up with a
            # product it doesn't already have a line for, since a human hasn't looked at it yet.
            existing_product_ids = {r["ProductID"] for r in db.query(
                "SELECT ProductID FROM StockIssueLines WHERE IssueID=?", (open_issue["IssueID"],))}
            for prod_id, qty, price, *_rest in line_data:
                if prod_id not in existing_product_ids:
                    expand_pending_stock_issue_for_product(open_issue, prod_id, qty, price, sale_date)
                    existing_product_ids.add(prod_id)

        for prod_id, qty, price, discount, taxable_value, hsn, gst_rate, gst in line_data:
            db.execute("""INSERT INTO SalesLines (SaleID, ProductID, Qty, UnitPrice, DiscountAmount, LineTotal, HSNCode,
                        GSTRate, TaxableValue, CGSTRate, CGSTAmount, SGSTRate, SGSTAmount, IGSTRate, IGSTAmount)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (sale_id, prod_id, qty, price, discount, taxable_value, hsn, gst_rate, taxable_value,
                        gst["cgst_rate"], gst["cgst_amt"], gst["sgst_rate"], gst["sgst_amt"],
                        gst["igst_rate"], gst["igst_amt"]))
            if status == "Completed" and post_inventory:
                remaining_qty = qty
                if open_issue:
                    sil = db.query("""SELECT LineID, QtyIssued, QtySold, QtyReturned, QtyFree FROM StockIssueLines
                                    WHERE IssueID=? AND ProductID=?""", (open_issue["IssueID"], prod_id), one=True)
                    if sil:
                        available = max((sil["QtyIssued"] or 0) - (sil["QtySold"] or 0)
                                         - (sil["QtyReturned"] or 0) - (sil["QtyFree"] or 0), 0)
                        apply_qty = min(remaining_qty, available)
                        if apply_qty > 0:
                            discount_share = round((discount or 0) * (apply_qty / qty), 2) if qty else 0
                            db.execute("""UPDATE StockIssueLines SET QtySold = COALESCE(QtySold, 0) + ?,
                                        DiscountAmount = COALESCE(DiscountAmount, 0) + ? WHERE LineID=?""",
                                       (apply_qty, discount_share, sil["LineID"]))
                            db.execute("""INSERT INTO SaleStockIssueLinks (SaleID, StockIssueLineID, QtyApplied, DiscountApplied)
                                        VALUES (?,?,?,?)""", (sale_id, sil["LineID"], apply_qty, discount_share))
                            remaining_qty = round(remaining_qty - apply_qty, 4)
                if remaining_qty > 0 and reconciled_issue:
                    sil2 = db.query("""SELECT QtyIssued, QtySold, QtyReturned, QtyFree FROM StockIssueLines
                                     WHERE IssueID=? AND ProductID=?""", (reconciled_issue["IssueID"], prod_id), one=True)
                    if sil2:
                        # true_product_qty_sold() filters by EmployeeID, so it only ever sees Sales
                        # that actually carry this employee's ID - it does NOT see the reconciled
                        # issue's own auto-invoiced "Unassigned" catch-all Sale (create_sale() there
                        # is deliberately called with no employee_id, since that Sale bills the
                        # system Unassigned customer, not a specific salesperson's own account). So
                        # sil2["QtySold"] (this line's frozen, already-reconciled figure - which IS
                        # what that anonymous catch-all Sale covers) has to be added in separately
                        # here, on top of true_product_qty_sold's employee-linked total for every
                        # OTHER (genuinely late) sale against this same day, or this capacity check
                        # would overcount by the whole original reconciled QtySold every time.
                        already_sold_other = round((sil2["QtySold"] or 0)
                                                    + true_product_qty_sold(employee_id, sale_date, prod_id) - qty, 4)
                        available2 = max((sil2["QtyIssued"] or 0) - (sil2["QtyReturned"] or 0)
                                          - (sil2["QtyFree"] or 0) - already_sold_other, 0)
                        covered_qty = min(remaining_qty, available2)
                        remaining_qty = round(remaining_qty - covered_qty, 4)
                if remaining_qty > 0:
                    direct_sale_txn_id = db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                                QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                               (prod_id, sale_date, "Sale", -remaining_qty, "Sale", sale_id, invoice_no))
                    db.execute("""INSERT INTO DirectSaleReviews (TransactionID, ProductID, Status)
                                VALUES (?,?,'Pending')""", (direct_sale_txn_id, prod_id))
        return sale_id


def true_product_qty_sold(employee_id, sale_date, product_id):
    """Ground-truth total quantity of a product sold by an employee on a date, computed
    directly from SalesLines/Sales - independent of StockIssueLines.QtySold. QtySold only
    accumulates through create_sale() crediting a Stock Issue line, which under-reports
    whenever that line's Qty Issued capacity was topped up in multiple installments during
    the day: any Sales-tab entry saved while the line's running credited total had already
    caught up to whatever was issued so far falls through to a direct warehouse deduction
    instead, and never gets added to QtySold even though the stock genuinely left. This
    function always reflects the real total regardless of that timing (see
    diagnose_stock_issue_topup_history.py for the diagnostic that uncovered this)."""
    row = db.query("""SELECT COALESCE(SUM(sl.Qty), 0) q FROM SalesLines sl
                    JOIN Sales s ON s.SaleID = sl.SaleID
                    WHERE s.EmployeeID=? AND s.SaleDate=? AND sl.ProductID=? AND s.Status<>'Cancelled'""",
                   (employee_id, sale_date, product_id), one=True)
    return row["q"] or 0


def already_direct_deducted_qty(employee_id, sale_date, product_id):
    """How much of `product_id` was already deducted straight from the
    warehouse (a "Sale"-type InventoryTransactions row - see
    true_product_qty_sold()'s docstring for exactly when a Sale falls
    through to this instead of crediting a Stock Issue) for `employee_id`
    on `sale_date`, before any Stock Issue existed yet to credit it
    against. A Stock Issue created - or topped up with this product -
    afterward, for the same employee/date/product, must not deduct this
    same physical stock a second time; see stock_issue_post_line()."""
    if not employee_id:
        return 0
    row = db.query("""SELECT COALESCE(SUM(-it.QtyChange), 0) q
                    FROM InventoryTransactions it
                    JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
                    WHERE it.ProductID=? AND it.TransactionType='Sale'
                      AND s.EmployeeID=? AND s.SaleDate=? AND s.Status<>'Cancelled'""",
                   (product_id, employee_id, sale_date), one=True)
    return row["q"] or 0


def direct_sales_today_for_employee(employee_id, sale_date, exclude_product_ids=()):
    """Every product (and qty) already sold straight from the warehouse for
    this employee/date, bypassing a Stock Issue entirely - shown as a
    heads-up when Issuing or topping up stock for them that day, so
    whoever enters Qty Issued knows some of that physical stock was
    already accounted for (the system also nets it automatically - see
    stock_issue_post_line() - this is purely informational). Products
    already on the Stock Issue in question are left out via
    exclude_product_ids, since those have already been netted once."""
    if not employee_id or not sale_date:
        return []
    rows = db.query("""SELECT it.ProductID, p.ProductName, SUM(-it.QtyChange) AS Qty
                     FROM InventoryTransactions it
                     JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
                     JOIN Products p ON p.ProductID = it.ProductID
                     WHERE it.TransactionType='Sale' AND s.EmployeeID=? AND s.SaleDate=? AND s.Status<>'Cancelled'
                     GROUP BY it.ProductID, p.ProductName
                     ORDER BY p.ProductName""", (employee_id, sale_date))
    if exclude_product_ids:
        rows = [r for r in rows if r["ProductID"] not in exclude_product_ids]
    return rows


def log_stock_issue_change(issue_id, action, field_name=None, old_value=None, new_value=None):
    """Records one row of Stock Issue history - who (the currently signed-in
    login, if any - a system-triggered auto-create still runs inside a
    salesperson's own request, so their login is what's captured), what
    changed, and the actual before/after value. Shown on the issue's own
    detail page (see stock_issue_view())."""
    user = get_current_user()
    db.execute(
        "INSERT INTO StockIssueAuditLog (IssueID, UserID, Username, Action, FieldName, OldValue, NewValue, CreatedAt) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (issue_id, user["UserID"] if user else None, user["Username"] if user else None,
         action, field_name, old_value, new_value, _now_ist().strftime("%Y-%m-%d %H:%M:%S")))


def stock_issue_post_line(issue_id, product_id, qty, price, txn_date, txn_notes, employee_id=None):
    """Issue (deduct stock for) qty of product_id against a Stock Issue, consolidated to one
    StockIssueLines row per product per issue rather than a separate row every time. If this
    issue already has a line for this product (from the original Issue Stock action or an
    earlier 'Add More Products' top-up on the same day), its Qty Issued is topped up in place
    and its Unit Price becomes the qty-weighted average of the old and new price, so a single
    consolidated line still produces the correct expected revenue at reconciliation. Otherwise a
    new line is created. Either way, a fresh 'Issue' InventoryTransactions row is posted for the
    qty being added, so stock deduction and the audit trail are unaffected by the consolidation.

    StockIssueLines.QtyIssued always stores the full physical quantity passed in - the honest
    "how much did this salesperson actually carry" figure that Reconcile's capacity/expected-
    revenue math depends on. But the warehouse InventoryTransactions deduction posted for it is
    reduced (floored at 0) by already_direct_deducted_qty() - whatever was already sold straight
    from the warehouse for this employee/product/date before this line existed (see that
    function's docstring) - so that same physical stock doesn't get deducted a second time, once
    as a direct Sale and again as this Issue. Only applied the FIRST time a line for this
    product is created on this issue (existing is None): a later top-up of an already-
    established line is genuinely new stock, not a re-count of the same already-netted qty.
    Returns the quantity netted out (0 if none), for the caller's flash message."""
    existing = db.query("SELECT LineID, QtyIssued, UnitPrice FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                        (issue_id, product_id), one=True)
    prod = db.query("SELECT ProductName FROM Products WHERE ProductID=?", (product_id,), one=True)
    prod_name = prod["ProductName"] if prod else f"Product #{product_id}"
    netted = 0
    if existing:
        old_qty = existing["QtyIssued"] or 0
        new_qty = old_qty + qty
        new_price = round((((existing["QtyIssued"] or 0) * (existing["UnitPrice"] or 0)) + (qty * price)) / new_qty, 4) \
            if new_qty else price
        db.execute("UPDATE StockIssueLines SET QtyIssued=?, UnitPrice=? WHERE LineID=?",
                   (new_qty, new_price, existing["LineID"]))
        log_stock_issue_change(issue_id, "Line Updated", f"Qty Issued ({prod_name})",
                                f"{old_qty:g}", f"{new_qty:g}")
    else:
        db.execute("""INSERT INTO StockIssueLines (IssueID, ProductID, QtyIssued, UnitPrice)
                    VALUES (?,?,?,?)""", (issue_id, product_id, qty, price))
        log_stock_issue_change(issue_id, "Line Added", prod_name, None, f"{qty:g} @ ₹{price:g}")
        already = already_direct_deducted_qty(employee_id, txn_date, product_id)
        netted = min(already, qty)
    deduct_qty = round(qty - netted, 4)
    db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
               (product_id, txn_date, "Issue", -deduct_qty, "StockIssue", issue_id, txn_notes))
    return netted


LOCATION_TZ = ZoneInfo("Asia/Kolkata")  # business hours are IST regardless of the server's own OS clock/timezone


def _now_ist():
    return datetime.now(LOCATION_TZ)


def _pdf_data_table(rows):
    """Builds one reportlab Table flowable from a list of dict-rows (columns
    = the first row's keys, in that order), or a 'No data' Paragraph if
    empty. Shared by rows_to_pdf() (one table) and rows_to_pdf_sections()
    (several tables, each on its own page)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("c", parent=styles["Normal"], fontSize=7.5, leading=9.5)
    header_style = ParagraphStyle("h", parent=styles["Normal"], fontSize=7.5, leading=9.5,
                                   textColor=colors.white, fontName="Helvetica-Bold")
    if not rows:
        return Paragraph("No data for this report.", styles["Normal"])

    headers = list(rows[0].keys())
    data = [[Paragraph(str(h), header_style) for h in headers]]
    for r in rows:
        data.append([Paragraph("" if r[h] is None else str(r[h]), cell_style) for h in headers])
    page_width = landscape(A4)[0] - 20 * mm
    col_width = page_width / len(headers)
    table = Table(data, colWidths=[col_width] * len(headers), repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def get_employee_month_target_progress(employee_id, year=None, month=None):
    """Convenience for other tabs (Dashboard, Stock Issues list/view, Reconcile)
    to show a lightweight Target-vs-Actual badge for one employee's whole-
    employee (all-products) target for a given month, without duplicating
    the fuller Targets-tab logic. Returns None if no such target is set."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    t = db.query("""SELECT * FROM Targets WHERE EmployeeID=? AND ProductID IS NULL
                  AND TargetYear=? AND TargetMonth=?""", (employee_id, year, month), one=True)
    if not t:
        return None
    days_elapsed = target_days_elapsed(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    return build_target_view(t, days_elapsed, days_in_month)


def get_custom_values_bulk(module, record_ids):
    """dict record_id -> {field_key: value}, for showing custom fields in list views."""
    record_ids = list(record_ids)
    if not record_ids:
        return {}
    placeholders = ",".join("?" * len(record_ids))
    rows = db.query(f"""SELECT v.RecordID, d.FieldKey, v.ValueText FROM CustomFieldValues v
                      JOIN CustomFieldDefinitions d ON d.FieldID = v.FieldID
                      WHERE d.ModuleName=? AND d.Active=1 AND v.RecordID IN ({placeholders})""",
                     (module, *record_ids))
    out = {}
    for r in rows:
        out.setdefault(r["RecordID"], {})[r["FieldKey"]] = r["ValueText"]
    return out
