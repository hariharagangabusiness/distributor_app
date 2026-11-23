"""reports_gst routes."""
import db
import depreciation
import gst_logic
import os
import re
import uuid
from core.auth import admin_required, get_current_user
from core.helpers import MONTH_NAMES, UPLOAD_ROOT, _pdf_data_table, _selected_month_from_request, expiring_documents, format_date_dmy, get_company_settings, get_company_target_summary, get_effective_columns, get_employee_month_target_progress, low_stock_products, over_stock_products, today_str, true_product_qty_sold, upcoming_maintenance
from datetime import date, datetime, timedelta
from flask import flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from formatting import indian_number_format
from werkzeug.utils import secure_filename



from flask import Blueprint

bp = Blueprint("reports_gst", __name__)


@bp.route("/reports/deleted-sales")
@admin_required
def deleted_sales_report():
    """Admin-only report of every Sale permanently removed via sale_delete(),
    read from DeletedSalesLog - the snapshot taken right before each
    deletion, since the Sale itself no longer exists afterward. Defaults
    to today; date_from/date_to (both inclusive, compared against
    DeletedAt) narrow the range. Deletions from before DeletedSalesLog
    existed simply aren't in here - there was nowhere for that data to
    have been kept."""
    today = today_str()
    date_from = request.args.get("date_from") or today
    date_to = request.args.get("date_to") or today
    rows = db.query("""SELECT * FROM DeletedSalesLog
                     WHERE date(DeletedAt) BETWEEN ? AND ?
                     ORDER BY DeletedAt DESC""", (date_from, date_to))
    totals = {
        "count": len(rows),
        "amount": round(sum(r["TotalAmount"] or 0 for r in rows), 2),
    }
    return render_template("deleted_sales_report.html", rows=rows, date_from=date_from, date_to=date_to,
                            today=today, totals=totals)


@bp.route("/reports/invoice-lookup")
@admin_required
def invoice_lookup_report():
    """Admin-only: look up one Sale by its InvoiceNumber and show full details -
    customer, salesperson, payment split, line items, and any Stock Issue it's
    credited against. The in-browser equivalent of lookup_invoice.py."""
    invoice_no = (request.args.get("invoice") or "").strip()
    sale = None
    lines = None
    linked_issues = None
    similar = None
    if invoice_no:
        sale = db.query("""SELECT s.*, c.CustomerName, c.Phone AS CustomerPhone, e.EmployeeName
                         FROM Sales s JOIN Customers c ON c.CustomerID = s.CustomerID
                         LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
                         WHERE s.InvoiceNumber = ?""", (invoice_no,), one=True)
        if sale:
            lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                              JOIN Products pr ON pr.ProductID = sl.ProductID WHERE sl.SaleID=?""",
                             (sale["SaleID"],))
            linked_issues = db.query("""SELECT DISTINCT sil.IssueID FROM SaleStockIssueLinks ssl
                                      JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                                      WHERE ssl.SaleID=?""", (sale["SaleID"],))
        else:
            similar = db.query("SELECT InvoiceNumber FROM Sales WHERE InvoiceNumber LIKE ? ORDER BY InvoiceNumber",
                               (f"%{invoice_no[-8:]}%",)) if len(invoice_no) >= 4 else []
    return render_template("invoice_lookup_report.html", invoice_no=invoice_no, sale=sale, lines=lines,
                            linked_issues=linked_issues, similar=similar)


@bp.route("/reports/product-qty-sold")
@admin_required
def product_qty_sold_report():
    """Admin-only: for a product/date, shows every customer who bought it (ground truth
    from SalesLines) plus, if any Stock Issue lines exist for it that day, whether their
    stored QtySold matches - flagging the under-reporting pattern this page was built to
    replace terminal scripts for (see true_product_qty_sold() and
    diagnose_stock_issue_topup_history.py)."""
    today = today_str()
    product_name = (request.args.get("product") or "").strip()
    report_date = request.args.get("date") or today
    product = None
    candidates = None
    sale_rows = None
    stock_issue_lines = None
    true_total = None
    if product_name:
        product = db.query("SELECT ProductID, ProductName, Unit FROM Products WHERE ProductName=?",
                           (product_name,), one=True)
        if not product:
            candidates = db.query("SELECT ProductID, ProductName FROM Products WHERE ProductName LIKE ? LIMIT 20",
                                  (f"%{product_name}%",))
        else:
            sale_rows = db.query("""SELECT c.CustomerName, s.InvoiceNumber, s.SaleID, e.EmployeeName, sl.Qty,
                                  (sl.TaxableValue + sl.CGSTAmount + sl.SGSTAmount + sl.IGSTAmount) AS LineTotal
                                  FROM SalesLines sl JOIN Sales s ON s.SaleID = sl.SaleID
                                  JOIN Customers c ON c.CustomerID = s.CustomerID
                                  LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
                                  WHERE sl.ProductID=? AND s.SaleDate=? AND s.Status<>'Cancelled'
                                  ORDER BY c.CustomerName""", (product["ProductID"], report_date))
            true_total = round(sum(r["Qty"] or 0 for r in sale_rows), 2)
            stock_issue_lines = db.query("""
                SELECT sil.LineID, sil.IssueID, sil.QtyIssued, sil.QtySold, si.Status, e.EmployeeName
                FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                JOIN Employees e ON e.EmployeeID = si.EmployeeID
                WHERE sil.ProductID=? AND si.IssueDate=? ORDER BY si.IssueID""",
                (product["ProductID"], report_date))
    return render_template("product_qty_sold_report.html", product_name=product_name, report_date=report_date,
                            today=today, product=product, candidates=candidates, sale_rows=sale_rows,
                            stock_issue_lines=stock_issue_lines, true_total=true_total)


@bp.route("/reports/stock-issue-topup-history")
@admin_required
def stock_issue_topup_history_report():
    """Admin-only: for a Stock Issue + product, lists every 'Issue' InventoryTransactions
    top-up posted against that line, in order, so you can see whether it was issued in one
    lump sum or several installments (the in-browser equivalent of
    diagnose_stock_issue_topup_history.py)."""
    issue_id = request.args.get("issue_id", type=int)
    product_id = request.args.get("product_id", type=int)
    issue = None
    product = None
    topups = None
    line = None
    running_total = None
    if issue_id and product_id:
        issue = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                          JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""",
                         (issue_id,), one=True)
        product = db.query("SELECT ProductName, Unit FROM Products WHERE ProductID=?", (product_id,), one=True)
        if issue and product:
            topups = db.query("""SELECT TransactionID, -QtyChange AS Qty, Notes, TransactionDate
                               FROM InventoryTransactions
                               WHERE RefType='StockIssue' AND RefID=? AND ProductID=? AND TransactionType='Issue'
                               ORDER BY TransactionID""", (issue_id, product_id))
            running = 0
            for t in topups:
                running += t["Qty"]
            running_total = running
            line = db.query("SELECT * FROM StockIssueLines WHERE IssueID=? AND ProductID=?",
                            (issue_id, product_id), one=True)
    all_issues = db.query("""SELECT si.IssueID, si.IssueDate, e.EmployeeName FROM StockIssues si
                           JOIN Employees e ON e.EmployeeID = si.EmployeeID ORDER BY si.IssueID DESC LIMIT 200""")
    return render_template("stock_issue_topup_report.html", issue_id=issue_id, product_id=product_id,
                            issue=issue, product=product, topups=topups, line=line,
                            running_total=running_total, all_issues=all_issues)


@bp.route("/reports/todays-discrepancy")
@admin_required
def todays_discrepancy_report():
    """Admin-only: for a date (defaults to today), every Stock Issue with a nonzero
    Discrepancy or a positive AmountDue, broken down line-by-line - each line's stored
    QtySold vs. the ground-truth true total sold (see true_product_qty_sold()), so a
    "total Amount Due shows less than what's actually owed" complaint can be traced to
    its cause in one place: usually a line whose QtySold under-reports because its Qty
    Issued capacity was topped up in several installments during the day (the same
    pattern fixed for Issue #28/TATA Copper+), which under-states Expected Rs and
    therefore Amount Due on that issue, even though the Discrepancy/Amount Due figures
    themselves are computed correctly from whatever QtySold happens to be stored."""
    report_date = request.args.get("date") or today_str()
    issues = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                       JOIN Employees e ON e.EmployeeID = si.EmployeeID
                       WHERE si.IssueDate=? ORDER BY si.IssueID""", (report_date,))

    issue_rows = []
    total_understated = 0.0
    for issue in issues:
        lines = db.query("""SELECT sil.*, pr.ProductName, pr.Unit FROM StockIssueLines sil
                          JOIN Products pr ON pr.ProductID = sil.ProductID WHERE sil.IssueID=?""",
                         (issue["IssueID"],))
        line_rows = []
        issue_understated = 0.0
        for l in lines:
            stored = l["QtySold"] or 0
            true_total = true_product_qty_sold(issue["EmployeeID"], issue["IssueDate"], l["ProductID"])
            gap = round(true_total - stored, 2)
            # Approximate Rs impact of the gap at this line's unit price - a straight
            # multiply, not netting out discount (a line's Discount Rs is only ever
            # recorded against whatever was credited, so the gap's own discount share
            # is unknown; treat this as an upper-bound estimate, not an exact figure)
            gap_amount = round(gap * (l["UnitPrice"] or 0), 2)
            if gap > 0.01:
                issue_understated += gap_amount
            line_rows.append({
                "ProductName": l["ProductName"], "Unit": l["Unit"], "QtyIssued": l["QtyIssued"],
                "StoredQtySold": stored, "TrueQtySold": true_total, "Gap": gap,
                "UnitPrice": l["UnitPrice"], "GapAmount": gap_amount,
            })
        issue_understated = round(issue_understated, 2)
        total_understated += issue_understated
        if (issue["Discrepancy"] or 0) != 0 or (issue["AmountDue"] or 0) > 0 or issue_understated > 0.01:
            issue_rows.append({
                "issue": issue, "lines": line_rows, "understated": issue_understated,
            })
    total_understated = round(total_understated, 2)
    return render_template("todays_discrepancy_report.html", report_date=report_date, today=today_str(),
                            issue_rows=issue_rows, total_understated=total_understated)


@bp.route("/reports/stock-issues")
def stock_issues_report():
    date_from = request.args.get("from") or date.today().replace(day=1).isoformat()
    date_to = request.args.get("to") or today_str()

    issues = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                       JOIN Employees e ON e.EmployeeID = si.EmployeeID
                       WHERE si.IssueDate BETWEEN ? AND ? ORDER BY si.IssueDate DESC, si.IssueID DESC""",
                       (date_from, date_to))

    totals = {
        "expected": sum(i["ExpectedAmount"] or 0 for i in issues),
        "collected": sum(i["CashCollected"] or 0 for i in issues),
        "scheme": sum(i["SchemeAmount"] or 0 for i in issues),
        "due": sum(i["AmountDue"] or 0 for i in issues),
    }
    due_issues = [i for i in issues if (i["AmountDue"] or 0) > 0]
    claim_pending = [i for i in issues if (i["SchemeAmount"] or 0) > 0 and i["ClaimStatus"] != "Received"]

    free_lines = db.query("""SELECT sil.*, pr.ProductName, si.IssueDate, e.EmployeeName FROM StockIssueLines sil
                           JOIN StockIssues si ON si.IssueID = sil.IssueID
                           JOIN Employees e ON e.EmployeeID = si.EmployeeID
                           JOIN Products pr ON pr.ProductID = sil.ProductID
                           WHERE si.IssueDate BETWEEN ? AND ?
                             AND (sil.QtyFree > 0 OR sil.DiscountAmount > 0 OR sil.SchemeClaimAmount > 0)
                           ORDER BY si.IssueDate DESC""", (date_from, date_to))

    # Day-wise Cost vs Sales - a plain reconciliation check, deliberately not
    # touching Incentive (that only belongs in the P&L margin view). Cost =
    # each SOLD unit's Product.CostPrice (QtySold, not QtyIssued - unsold
    # stock issued that day carries no cost of goods sold yet); Sales = the
    # Stock Issue's own Expected/Collected amounts for that day.
    daywise_cost_rows = db.query("""SELECT si.IssueDate AS d, COALESCE(SUM(sil.QtySold * pr.CostPrice), 0) AS cost
                                  FROM StockIssueLines sil
                                  JOIN StockIssues si ON si.IssueID = sil.IssueID
                                  JOIN Products pr ON pr.ProductID = sil.ProductID
                                  WHERE si.IssueDate BETWEEN ? AND ?
                                  GROUP BY si.IssueDate""", (date_from, date_to))
    daywise_sales_rows = db.query("""SELECT IssueDate AS d, COALESCE(SUM(ExpectedAmount), 0) AS expected,
                                   COALESCE(SUM(CashCollected), 0) AS collected
                                   FROM StockIssues WHERE IssueDate BETWEEN ? AND ? GROUP BY IssueDate""",
                                  (date_from, date_to))
    daywise_line_rows = db.query("""SELECT si.IssueDate AS d,
                                  COALESCE(SUM(sil.QtySold), 0) AS qty_sold,
                                  COALESCE(SUM(sil.DiscountAmount), 0) AS discount,
                                  COALESCE(SUM(sil.SchemeClaimAmount), 0) AS scheme,
                                  COALESCE(SUM(sil.QtyIssued - sil.QtySold - sil.QtyReturned - sil.QtyFree), 0) AS unaccounted
                                  FROM StockIssueLines sil
                                  JOIN StockIssues si ON si.IssueID = sil.IssueID
                                  WHERE si.IssueDate BETWEEN ? AND ?
                                  GROUP BY si.IssueDate""", (date_from, date_to))
    cost_by_day = {r["d"]: r["cost"] for r in daywise_cost_rows}
    sales_by_day = {r["d"]: {"expected": r["expected"], "collected": r["collected"]} for r in daywise_sales_rows}
    lines_by_day = {r["d"]: {"qty_sold": r["qty_sold"], "discount": r["discount"], "scheme": r["scheme"],
                              "unaccounted": r["unaccounted"]} for r in daywise_line_rows}
    all_days = sorted(set(cost_by_day) | set(sales_by_day) | set(lines_by_day), reverse=True)
    daywise = []
    for d in all_days:
        cost = round(cost_by_day.get(d, 0), 2)
        expected = round(sales_by_day.get(d, {}).get("expected", 0), 2)
        collected = round(sales_by_day.get(d, {}).get("collected", 0), 2)
        line_agg = lines_by_day.get(d, {})
        daywise.append({"date": d, "cost": cost, "expected": expected, "collected": collected,
                         "margin": round(expected - cost, 2),
                         "qty_sold": round(line_agg.get("qty_sold", 0), 2),
                         "discount": round(line_agg.get("discount", 0), 2),
                         "scheme": round(line_agg.get("scheme", 0), 2),
                         "unaccounted": round(line_agg.get("unaccounted", 0), 2)})

    return render_template("stock_issues_report.html", date_from=date_from, date_to=date_to,
                            issues=issues, totals=totals, due_issues=due_issues,
                            claim_pending=claim_pending, free_lines=free_lines, daywise=daywise)


# ---------------------------------------------------------------------
# Real-time Sales Monitoring — one row per salesperson (Employee) for a
# single day (defaults to today), pulling together everything scattered
# across Stock Issues + Sales for that person on that day into one live
# view: stock issued/sold/returned/free/unaccounted, direct Sales-tab
# entries (count/total/cash/bank/discount), reconciliation cash/bank (if
# already reconciled that day), a combined Expected-vs-Collected
# discrepancy, and month-target pace. Auto-refreshes client-side so an
# Admin/Manager can leave it open during the day.
# ---------------------------------------------------------------------


@bp.route("/reports/sales-live")
def sales_live_report():
    date_str = request.args.get("date") or today_str()
    y, m = int(date_str[:4]), int(date_str[5:7])

    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")

    issues_by_emp = {}
    for i in db.query("SELECT * FROM StockIssues WHERE IssueDate=?", (date_str,)):
        issues_by_emp.setdefault(i["EmployeeID"], []).append(i)

    line_agg_rows = db.query("""SELECT si.EmployeeID,
                              COALESCE(SUM(sil.QtyIssued), 0) AS qty_issued,
                              COALESCE(SUM(sil.QtySold), 0) AS qty_sold,
                              COALESCE(SUM(sil.QtyReturned), 0) AS qty_returned,
                              COALESCE(SUM(sil.QtyFree), 0) AS qty_free,
                              COALESCE(SUM(sil.QtyIssued - COALESCE(sil.QtySold,0) - COALESCE(sil.QtyReturned,0)
                                        - COALESCE(sil.QtyFree,0)), 0) AS unaccounted,
                              COALESCE(SUM(sil.QtySold * sil.UnitPrice - COALESCE(sil.DiscountAmount,0)), 0) AS expected_live,
                              COALESCE(SUM(sil.DiscountAmount), 0) AS discount_from_lines
                              FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                              WHERE si.IssueDate=? GROUP BY si.EmployeeID""", (date_str,))
    lines_by_emp = {r["EmployeeID"]: r for r in line_agg_rows}

    sales_agg_rows = db.query("""SELECT EmployeeID, COUNT(*) n, COALESCE(SUM(TotalAmount),0) total,
                               COALESCE(SUM(CashAmount),0) cash, COALESCE(SUM(BankAmount),0) bank
                               FROM Sales WHERE SaleDate=? AND Status<>'Cancelled' AND EmployeeID IS NOT NULL
                               GROUP BY EmployeeID""", (date_str,))
    sales_by_emp = {r["EmployeeID"]: r for r in sales_agg_rows}

    discount_agg_rows = db.query("""SELECT s.EmployeeID, COALESCE(SUM(sl.DiscountAmount),0) discount
                                  FROM Sales s JOIN SalesLines sl ON sl.SaleID = s.SaleID
                                  WHERE s.SaleDate=? AND s.Status<>'Cancelled' AND s.EmployeeID IS NOT NULL
                                  GROUP BY s.EmployeeID""", (date_str,))
    discount_by_emp = {r["EmployeeID"]: r["discount"] for r in discount_agg_rows}

    # Ground-truth total Qty Sold per employee for the day, the same way true_product_qty_sold()
    # is ground truth per-product on the Reconcile/view pages - StockIssueLines.QtySold (what
    # line_agg_rows above sums) under-reports whenever a Sale fell through to a direct warehouse
    # deduction (its line's running credited total had already caught up to what was issued so
    # far when that Sale was saved - see true_product_qty_sold()'s docstring). Qty Sold and
    # Unaccounted below are corrected against this so "Unaccounted" doesn't look inflated by
    # units that were, in fact, genuinely sold and invoiced.
    true_qty_sold_rows = db.query("""SELECT s.EmployeeID, COALESCE(SUM(sl.Qty),0) qty_sold
                                   FROM Sales s JOIN SalesLines sl ON sl.SaleID = s.SaleID
                                   WHERE s.SaleDate=? AND s.Status<>'Cancelled' AND s.EmployeeID IS NOT NULL
                                   GROUP BY s.EmployeeID""", (date_str,))
    true_qty_sold_by_emp = {r["EmployeeID"]: r["qty_sold"] for r in true_qty_sold_rows}

    # Cash/Bank already folded into a Reconciled Stock Issue's own CashAmount/BankAmount via
    # the credited Sales that Reconcile's form is now pre-filled from (see
    # stock_issue_reconcile()) - excluded below from the plain per-Sale sum so Collected isn't
    # double-counted (once for the individual Sale, again for the reconciliation total that
    # already includes it).
    credited_reconciled_rows = db.query("""
        SELECT EmployeeID, COALESCE(SUM(CashAmount),0) cash, COALESCE(SUM(BankAmount),0) bank FROM (
          SELECT DISTINCT si.EmployeeID AS EmployeeID, s.SaleID AS SaleID,
                 s.CashAmount AS CashAmount, s.BankAmount AS BankAmount
          FROM SaleStockIssueLinks ssl
          JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
          JOIN StockIssues si ON si.IssueID = sil.IssueID
          JOIN Sales s ON s.SaleID = ssl.SaleID
          WHERE si.IssueDate=? AND si.Status='Reconciled'
        ) t GROUP BY EmployeeID""", (date_str,))
    credited_reconciled_by_emp = {r["EmployeeID"]: r for r in credited_reconciled_rows}

    top_products_rows = db.query("""SELECT s.EmployeeID, p.ProductName, SUM(sl.Qty) qty
                                  FROM Sales s JOIN SalesLines sl ON sl.SaleID = s.SaleID
                                  JOIN Products p ON p.ProductID = sl.ProductID
                                  WHERE s.SaleDate=? AND s.Status<>'Cancelled' AND s.EmployeeID IS NOT NULL
                                  GROUP BY s.EmployeeID, p.ProductID ORDER BY s.EmployeeID, qty DESC""", (date_str,))
    top_products_by_emp = {}
    for r in top_products_rows:
        top_products_by_emp.setdefault(r["EmployeeID"], []).append(r)

    rows = []
    for e in employees:
        emp_issues = issues_by_emp.get(e["EmployeeID"], [])
        la = lines_by_emp.get(e["EmployeeID"])
        sa = sales_by_emp.get(e["EmployeeID"])
        recon_cash = sum((i["CashAmount"] or 0) for i in emp_issues if i["Status"] == "Reconciled")
        recon_bank = sum((i["BankAmount"] or 0) for i in emp_issues if i["Status"] == "Reconciled")
        sales_cash = sa["cash"] if sa else 0
        sales_bank = sa["bank"] if sa else 0
        expected_live = round(la["expected_live"], 2) if la else 0
        if any(i["Status"] == "Reconciled" for i in emp_issues):
            # Reconciled: the recon total already covers every credited Sale's cash/bank (it's
            # pre-filled from exactly that sum) - only add whatever Sales money DIDN'T flow into
            # this reconciliation (a walk-in sale on the same day with no Stock Issue credit, etc).
            cr = credited_reconciled_by_emp.get(e["EmployeeID"])
            unlinked_cash = max(sales_cash - (cr["cash"] if cr else 0), 0)
            unlinked_bank = max(sales_bank - (cr["bank"] if cr else 0), 0)
            collected_live = round(recon_cash + recon_bank + unlinked_cash + unlinked_bank, 2)
            # Once reconciled, StockIssueLines.DiscountAmount (la["discount_from_lines"]) is
            # meant to be the authoritative full-day figure - Reconcile's GET handler now
            # pre-fills it from true_product_discount_sold(), the same ground truth
            # discount_by_emp is built from, so in the normal case they already agree and
            # there's nothing to add. But an issue reconciled BEFORE that pre-fill existed
            # can still have a stale, lower discount_from_lines than the true Sales-tab
            # total (discount_by_emp) - e.g. a sale that fell to a direct warehouse
            # deduction and was never folded in. Rather than trying to work out exactly how
            # much of discount_by_emp is "new" (which double-counted once pre-filling
            # started doing that same job - a sale's own discount would get added once via
            # the line and again as "unlinked"), simply take whichever total is higher: an
            # Admin-typed figure above the true total is deliberately kept, and a stale
            # figure below it is bridged up to ground truth without double-adding anything.
            issue_discount = la["discount_from_lines"] if la else 0
            discount_live = round(max(issue_discount, discount_by_emp.get(e["EmployeeID"], 0)), 2)
        else:
            collected_live = round(sales_cash + sales_bank, 2)
            discount_live = round(discount_by_emp.get(e["EmployeeID"], 0), 2)
        if not emp_issues and not sa:
            issue_badge = "No activity"
        elif any(i["ReviewStatus"] == "Pending" for i in emp_issues):
            issue_badge = "Pending Review"
        elif any(i["Status"] == "Reconciled" for i in emp_issues):
            issue_badge = "Reconciled"
        elif emp_issues:
            issue_badge = "Issued"
        else:
            issue_badge = "Sales only (no Stock Issue)"
        qty_issued_live = round(la["qty_issued"], 2) if la else 0
        qty_returned_live = round(la["qty_returned"], 2) if la else 0
        qty_free_live = round(la["qty_free"], 2) if la else 0
        # Ground truth (see true_qty_sold_by_emp above) is always >= the raw stored sum - max()
        # here is just defensive, matching the same pattern discount_live already uses.
        qty_sold_live = round(max(la["qty_sold"] if la else 0, true_qty_sold_by_emp.get(e["EmployeeID"], 0)), 2)
        rows.append({
            "employee": e,
            "issue_badge": issue_badge,
            "issue_ids": [i["IssueID"] for i in emp_issues],
            "qty_issued": qty_issued_live,
            "qty_sold": qty_sold_live,
            "qty_returned": qty_returned_live,
            "qty_free": qty_free_live,
            "unaccounted": round(qty_issued_live - qty_sold_live - qty_returned_live - qty_free_live, 2),
            "sales_count": sa["n"] if sa else 0,
            "sales_total": round(sa["total"], 2) if sa else 0,
            "sales_cash": round(sales_cash, 2),
            "sales_bank": round(sales_bank, 2),
            "discount": discount_live,
            "recon_cash": round(recon_cash, 2),
            "recon_bank": round(recon_bank, 2),
            "expected_live": expected_live,
            "collected_live": collected_live,
            "discrepancy_live": round(collected_live - expected_live, 2),
            "top_products": top_products_by_emp.get(e["EmployeeID"], [])[:3],
            "target_progress": get_employee_month_target_progress(e["EmployeeID"], y, m),
        })
    # Salespeople with today's activity float to the top; fully idle ones sink to the bottom.
    rows.sort(key=lambda r: (r["issue_badge"] == "No activity", r["employee"]["EmployeeName"]))

    # Sales entered today with no salesperson at all: plain counter sales, and/or the
    # auto-created "Unassigned" sale from a reconciliation. That auto-created sale's
    # Cash/Bank is the SAME money already counted above as recon_cash/recon_bank on its
    # salesperson's row (StockIssues.CashAmount/BankAmount and the Sale it creates are
    # two records of one reconciliation) - so it must be excluded here, or counting both
    # doubles the day's "Collected" total. Sale value/count still include it (it's real,
    # already-invoiced business), just not its cash/bank a second time.
    other_sales = db.query("""SELECT COUNT(*) n, COALESCE(SUM(TotalAmount),0) total
                            FROM Sales WHERE SaleDate=? AND Status<>'Cancelled' AND EmployeeID IS NULL""",
                            (date_str,), one=True)
    other_sales_unlinked_money = db.query("""SELECT COALESCE(SUM(CashAmount),0) cash, COALESCE(SUM(BankAmount),0) bank
                                           FROM Sales WHERE SaleDate=? AND Status<>'Cancelled' AND EmployeeID IS NULL
                                           AND SaleID NOT IN (SELECT SaleID FROM StockIssues WHERE SaleID IS NOT NULL)""",
                                           (date_str,), one=True)
    other_sales = dict(other_sales)
    other_sales["cash"] = other_sales_unlinked_money["cash"]
    other_sales["bank"] = other_sales_unlinked_money["bank"]

    totals = {
        "qty_issued": sum(r["qty_issued"] for r in rows),
        "qty_sold": sum(r["qty_sold"] for r in rows),
        "unaccounted": sum(r["unaccounted"] for r in rows),
        "sales_count": sum(r["sales_count"] for r in rows) + (other_sales["n"] or 0),
        "sales_total": round(sum(r["sales_total"] for r in rows) + (other_sales["total"] or 0), 2),
        "collected": round(sum(r["collected_live"] for r in rows) + (other_sales["cash"] or 0) + (other_sales["bank"] or 0), 2),
        "expected": round(sum(r["expected_live"] for r in rows), 2),
        "discount": round(sum(r["discount"] for r in rows), 2),
        "pending_review_count": sum(1 for r in rows if r["issue_badge"] == "Pending Review"),
        "no_activity_count": sum(1 for r in rows if r["issue_badge"] == "No activity"),
    }

    return render_template("sales_live_report.html", date_str=date_str, today=today_str(),
                            rows=rows, other_sales=other_sales, totals=totals,
                            columns=get_effective_columns("SalesLive"))


def rows_to_pdf_sections(sections, title="Report"):
    """Like rows_to_pdf(), but for a report that needs more than one table -
    each (section_title, rows) pair in `sections` becomes its own heading +
    table, with every section after the first starting on a fresh page.
    Used by the Accounts Receivable Invoice-wise PDF (a Customer-level
    summary first, then Invoice-level detail)."""
    import io
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Normal"], fontSize=14, alignment=TA_CENTER, fontName="Helvetica-Bold")
    section_style = ParagraphStyle("s", parent=styles["Normal"], fontSize=12, fontName="Helvetica-Bold", spaceAfter=6)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=12 * mm, bottomMargin=12 * mm,
                             leftMargin=10 * mm, rightMargin=10 * mm)
    story = [Paragraph(title, title_style), Spacer(1, 6),
              Paragraph(f"Generated {today_str()}", styles["Normal"]), Spacer(1, 10)]
    for i, (section_title, section_rows) in enumerate(sections):
        if i > 0:
            story.append(PageBreak())
        story.append(Paragraph(section_title, section_style))
        story.append(_pdf_data_table(section_rows))
    doc.build(story)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------
# Reports hub - a Dashboard-linked landing page giving a quick numeric
# breakdown/analysis per tab (Sales, Purchases, Stock, Expenses, Stock
# Issues, Employees/Salary, Scheme Claims, Customers, Fleet), each with
# Export to Excel/PDF for that tab's full data right there.
# ---------------------------------------------------------------------


@bp.route("/reports")
def reports_hub():
    today = date.today()
    sel = _selected_month_from_request()
    month_start, month_end = sel["month_start"], sel["month_end"]
    is_admin = bool(get_current_user() and get_current_user()["Role"] == "Admin")

    sales_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) t, COUNT(*) n FROM Sales WHERE SaleDate BETWEEN ? AND ? AND Status<>'Cancelled'",
                            (month_start, month_end), one=True)
    top_customers = db.query("""SELECT c.CustomerName, SUM(s.TotalAmount) total FROM Sales s
                              JOIN Customers c ON c.CustomerID=s.CustomerID
                              WHERE s.Status<>'Cancelled' AND c.IsUnassignedBucket=0
                              GROUP BY s.CustomerID ORDER BY total DESC LIMIT 5""")
    sales_trend = db.query("""SELECT strftime('%Y-%m', SaleDate) ym, SUM(TotalAmount) total FROM Sales
                            WHERE Status<>'Cancelled' GROUP BY ym ORDER BY ym DESC LIMIT 6""")

    purchases_month = db.query("SELECT COALESCE(SUM(TotalAmount),0) t, COUNT(*) n FROM Purchases WHERE PurchaseDate BETWEEN ? AND ? AND Status<>'Cancelled'",
                                (month_start, month_end), one=True)
    top_suppliers = db.query("""SELECT s.SupplierName, SUM(p.TotalAmount) total FROM Purchases p
                              JOIN Suppliers s ON s.SupplierID=p.SupplierID
                              WHERE p.Status<>'Cancelled' GROUP BY p.SupplierID
                              ORDER BY total DESC LIMIT 5""") if is_admin else []

    stock_summary = db.query("""SELECT COUNT(*) n, COALESCE(SUM(COALESCE(t.CurrentStock,0) * p.CostPrice),0) value
                              FROM Products p LEFT JOIN (
                                SELECT ProductID, SUM(QtyChange) CurrentStock FROM InventoryTransactions GROUP BY ProductID
                              ) t ON t.ProductID=p.ProductID WHERE p.Active=1""", one=True)
    low_stock_count = len(low_stock_products())
    over_stock_count = len(over_stock_products())
    scheme_product_count = db.query("SELECT COUNT(*) n FROM Products WHERE SchemePercent > 0 AND Active=1", one=True)["n"]
    # Category-wise total Qty Stock, alongside the overall total, for the Products & Stock card.
    category_stock = db.query("""SELECT COALESCE(p.Category, 'Uncategorized') AS category,
                               COALESCE(SUM(COALESCE(t.CurrentStock, 0)), 0) AS qty
                               FROM Products p LEFT JOIN (
                                 SELECT ProductID, SUM(QtyChange) CurrentStock FROM InventoryTransactions GROUP BY ProductID
                               ) t ON t.ProductID = p.ProductID
                               WHERE p.Active=1 GROUP BY COALESCE(p.Category, 'Uncategorized') ORDER BY qty DESC""")
    total_qty_stock = sum(r["qty"] for r in category_stock)

    expenses_month_total = db.query("SELECT COALESCE(SUM(Amount),0) t FROM Expenses WHERE ExpenseDate BETWEEN ? AND ?",
                                     (month_start, month_end), one=True)["t"]
    expenses_by_category = db.query("""SELECT Category, SUM(Amount) total FROM Expenses WHERE ExpenseDate BETWEEN ? AND ?
                                     GROUP BY Category ORDER BY total DESC""", (month_start, month_end))

    stock_issues_month = db.query("""SELECT COALESCE(SUM(ExpectedAmount),0) expected, COALESCE(SUM(CashCollected),0) collected,
                                   COALESCE(SUM(SchemeAmount),0) scheme, COALESCE(SUM(AmountDue),0) due
                                   FROM StockIssues WHERE IssueDate BETWEEN ? AND ?""", (month_start, month_end), one=True)

    salary_summary = None
    scheme_claims_summary = None
    if is_admin:
        salary_summary = db.query("""SELECT COALESCE(SUM(NetPayable),0) total,
                                   SUM(CASE WHEN Status='Pending' THEN 1 ELSE 0 END) pending,
                                   SUM(CASE WHEN Status='Paid' THEN 1 ELSE 0 END) paid
                                   FROM SalaryPayments WHERE SalaryYear=? AND SalaryMonth=?""",
                                   (sel["year"], sel["month"]), one=True)
        scheme_claims_summary = db.query("""SELECT
                                          COALESCE(SUM(CASE WHEN Status IN ('Pending','Claimed') THEN ClaimAmount ELSE 0 END),0) pending,
                                          COALESCE(SUM(CASE WHEN Status IN ('Received','Completed')
                                                             THEN COALESCE(ReceivedAmount, ClaimAmount) ELSE 0 END),0) received
                                          FROM SchemeClaims""", one=True)

    fleet_due = len(upcoming_maintenance()) + len(expiring_documents())
    active_customers = db.query("SELECT COUNT(*) n FROM Customers WHERE Active=1 AND IsUnassignedBucket=0", one=True)["n"]
    target_summary = get_company_target_summary(sel["year"], sel["month"])

    return render_template("reports_hub.html", is_admin=is_admin, target_summary=target_summary,
                            sales_month=sales_month, top_customers=top_customers, sales_trend=sales_trend,
                            purchases_month=purchases_month, top_suppliers=top_suppliers,
                            stock_summary=stock_summary, low_stock_count=low_stock_count,
                            over_stock_count=over_stock_count, scheme_product_count=scheme_product_count,
                            category_stock=category_stock, total_qty_stock=total_qty_stock,
                            expenses_month_total=expenses_month_total, expenses_by_category=expenses_by_category,
                            stock_issues_month=stock_issues_month, salary_summary=salary_summary,
                            scheme_claims_summary=scheme_claims_summary, fleet_due=fleet_due,
                            active_customers=active_customers, month_name=MONTH_NAMES[sel["month"]], year=sel["year"],
                            is_current_month=sel["is_current"],
                            prev_year=sel["prev_year"], prev_month=sel["prev_month"],
                            next_year=sel["next_year"], next_month=sel["next_month"])


# =======================================================================
# GST FILING  (GSTR-1 / GSTR-2B / GSTR-3B reference reports + reminders)
# =======================================================================
# See gst_logic.py's module docstring for the important caveats: due
# dates can change by government notification, and these figures are a
# computed reference/working paper, not a filing or a live pull from the
# GST portal.


GSTR2B_UPLOAD_DIR = "Gstr2b"  # folder name under uploads/, same convention as ATTACHMENT_MODULES


def _year_month_from_request():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    return year, month


# ---------------------------------------------------------------------
# Reports: Profit & Loss
#
# Computed on the fly from what's already tracked elsewhere - nothing new
# is entered here, and nothing is stored:
#   Revenue          = Sales.TaxableAmount (GST-exclusive) for Completed
#                       sales in the date range.
#   Cost of Goods Sold = each sold line's quantity x that product's CURRENT
#                       Products.CostPrice. This is a simplification: cost
#                       price isn't tracked historically per sale, so if a
#                       product's cost has changed since a past sale, that
#                       sale's COGS here uses today's cost rather than
#                       whatever it actually cost back then.
#   Operating Expenses = Expenses.Amount in the date range.
#   Salary Payouts    = SalaryPayments.NetPayable for payments marked Paid
#                       with a PaymentDate in the date range.
# Gross Profit = Revenue - COGS. Net Profit = Gross Profit - Expenses -
# Salary. This is a working-paper view for the business, not a statutory
# filing - it doesn't attempt double-entry bookkeeping, depreciation, or
# accrual adjustments.
# ---------------------------------------------------------------------


def ar_age_days(d):
    """Days overdue from a PaymentDueDate/SaleDate string, for Accounts
    Receivable - shared by the Invoice-wise tab and its PDF export so both
    always compute it identically."""
    if not d:
        return 0
    try:
        return (date.today() - date.fromisoformat(d[:10])).days
    except ValueError:
        return 0


def get_ar_base_rows():
    """Every unpaid/partially-paid invoice, across all time (dues don't
    expire at a month boundary) - the shared source data behind every
    Accounts Receivable view and its PDF export. See accounts_receivable()
    for the full definition of "due" used here.

    Only Completed sales count (not Draft - it isn't billed/sent to the
    customer yet, so there's nothing yet to chase payment for; not
    Cancelled - it never was)."""
    return db.query("""
        SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.PaymentDueDate, s.PaymentStatus,
               s.TotalAmount, s.TaxableAmount, s.AmountReceived, (s.TaxableAmount - s.AmountReceived) AS Due,
               c.CustomerID, c.CustomerName, c.Phone, c.Zone, c.AccountNumber,
               p.CustomerName AS ParentCustomerName, p.AccountNumber AS ParentAccountNumber,
               e.EmployeeName
        FROM Sales s
        JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Customers p ON p.CustomerID = c.ParentCustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE s.Status = 'Completed' AND c.IsUnassignedBucket = 0
          AND (s.TaxableAmount - s.AmountReceived) > 0.005
        ORDER BY s.PaymentDueDate IS NULL, s.PaymentDueDate, s.SaleDate
    """)


def get_ar_invoice_rows(base_rows=None):
    """get_ar_base_rows(), shaped exactly as the Invoice-wise tab/PDF need
    it: plain dicts with AgeDays added and Due rounded. Pass an already-
    fetched base_rows to avoid querying twice when a caller also needs
    get_ar_customer_summary() from the same data."""
    if base_rows is None:
        base_rows = get_ar_base_rows()
    rows = [dict(r) for r in base_rows]
    for r in rows:
        r["AgeDays"] = ar_age_days(r["PaymentDueDate"] or r["SaleDate"])
        r["Due"] = round(r["Due"], 2)
    return rows


def get_ar_customer_summary(base_rows=None, sort="due"):
    """Per-customer aggregate (invoiced/taxable/received/due/oldest due
    date) from get_ar_base_rows() - shared by the Customer Summary tab and
    the Invoice-wise PDF's first section. sort='due' (default, matches the
    on-screen tab) sorts by due descending; sort='name' sorts alphabetically
    by customer name, for the PDF."""
    if base_rows is None:
        base_rows = get_ar_base_rows()
    by_customer = {}
    for r in base_rows:
        agg = by_customer.setdefault(r["CustomerID"], {
            "CustomerID": r["CustomerID"], "CustomerName": r["CustomerName"], "Phone": r["Phone"],
            "Zone": r["Zone"], "AccountNumber": r["AccountNumber"],
            "ParentCustomerName": r["ParentCustomerName"], "ParentAccountNumber": r["ParentAccountNumber"],
            "invoiced": 0, "taxable": 0, "received": 0, "due": 0, "invoice_count": 0,
            "oldest_due_date": None,
        })
        agg["invoiced"] += r["TotalAmount"]
        agg["taxable"] += r["TaxableAmount"]
        agg["received"] += r["AmountReceived"]
        agg["due"] += r["Due"]
        agg["invoice_count"] += 1
        d = r["PaymentDueDate"] or r["SaleDate"]
        if d and (agg["oldest_due_date"] is None or d < agg["oldest_due_date"]):
            agg["oldest_due_date"] = d
    rows = list(by_customer.values())
    if sort == "name":
        rows.sort(key=lambda a: (a["CustomerName"] or "").lower())
    else:
        rows.sort(key=lambda a: -a["due"])
    for a in rows:
        a["invoiced"] = round(a["invoiced"], 2)
        a["taxable"] = round(a["taxable"], 2)
        a["received"] = round(a["received"], 2)
        a["due"] = round(a["due"], 2)
    return rows


def _ar_customer_pdf_row(c):
    return {
        "Account #": c["AccountNumber"] or "-",
        "Customer": c["CustomerName"],
        "Phone": c["Phone"] or "-",
        "Zone": c["Zone"] or "-",
        "# Invoices": c["invoice_count"],
        "Taxable Value": indian_number_format(c["taxable"]),
        "Invoiced (incl. GST)": indian_number_format(c["invoiced"]),
        "Received": indian_number_format(c["received"]),
        "Due": indian_number_format(c["due"]),
    }


def _ar_invoice_pdf_row(r):
    # Deliberately no Salesperson/Due Date - not needed for this export.
    return {
        "Invoice #": r["InvoiceNumber"],
        "Account #": r["AccountNumber"] or "-",
        "Customer": r["CustomerName"],
        "Phone": r["Phone"] or "-",
        "Zone": r["Zone"] or "-",
        "Sale Date": format_date_dmy(r["SaleDate"]),
        "Age (days)": r["AgeDays"],
        "Status": r["PaymentStatus"],
        "Taxable Value": indian_number_format(r["TaxableAmount"]),
        "Total (incl. GST)": indian_number_format(r["TotalAmount"]),
        "Received": indian_number_format(r["AmountReceived"]),
        "Due": indian_number_format(r["Due"]),
    }


# ---------------------------------------------------------------------
# Accounts Receivable: payment reminders (Call / WhatsApp)
#
# A "Remind" button next to an invoice (Invoice-wise tab) or a customer's
# whole balance (Customer Summary tab) opens the device's own dialer or
# WhatsApp with a pre-filled message - nothing is sent automatically or
# through any paid gateway. ARReminderLog records that it happened (who,
# when, which channel, against which invoice/customer), which drives the
# "Reminder due" flag: overdue past ARReminderOverdueDays AND not already
# reminded within that same window. An automated SMS/voice-call channel
# would need a chosen provider + API key and isn't wired up here - this
# only covers the two channels that need no account/cost to use.
# ---------------------------------------------------------------------


def _ar_phone_e164(phone):
    """Best-effort normalize a saved Phone value to a bare international-format number
    (country code + number, digits only, no '+') for tel:/WhatsApp links. Customer/Employee
    phone fields throughout this app are plain text with no format enforcement - assumes
    India (+91) for what looks like a bare 10-digit mobile number, since that's what's
    actually typed in practice. Returns None if there's nothing usable."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if len(digits) == 13 and digits.startswith("091"):
        return digits[1:]
    return digits if 10 <= len(digits) <= 15 else None


def _ar_reminder_message(customer_name, due_amount, invoice_number=None, age_days=None):
    company_name = get_company_settings()["CompanyName"] or "us"
    amount = indian_number_format(due_amount)
    if invoice_number:
        age_note = f", which is now {age_days} day(s) overdue" if age_days else ""
        due_note = f"against Invoice No. {invoice_number}{age_note}"
    else:
        due_note = "on your account with us"
    return (f"This is a reminder from {company_name} that an outstanding balance of ₹{amount} remains due "
            f"{due_note}.\n\n"
            "We kindly request you to arrange for the settlement at your earliest convenience. Our bank "
            "account details and UPI QR code are provided on the invoice for easy payment. If you have "
            "already processed this payment, please disregard this message and share the transaction "
            "reference details.\n\n"
            "Thank you for your prompt attention.")


def get_ar_reminder_flags(invoice_rows, customer_summary):
    """Adds ReminderDue (bool) to every invoice_rows/customer_summary row in place.

    An invoice up to ARReminderDailyAfterDays overdue is flagged once it crosses
    ARReminderOverdueDays AND hasn't had a reminder logged in that same window (so the
    re-reminder cadence matches the configured threshold, as before). Past
    ARReminderDailyAfterDays, it's flagged daily instead - a badly overdue invoice needs a
    reminder every day it stays unpaid, not just once every `threshold` days.

    A customer (Customer Summary tab) is flagged whenever ANY of their own invoices above is -
    so reminding every one of a customer's overdue invoices from the Invoice-wise tab (one at a
    time, or in bulk) also clears their Customer Summary badge. The account-level Call/WhatsApp
    button logs its reminder against each of that customer's currently outstanding invoices
    instead of one account-wide entry (see ar_remind()), so acting from Customer Summary clears
    the same per-invoice flags in the other direction too - both tabs always agree.

    Also adds TelLink (None if the saved Phone can't be normalized) and ReminderMessage."""
    settings = get_company_settings()
    threshold = settings["ARReminderOverdueDays"] or 7
    daily_after = settings["ARReminderDailyAfterDays"] or 14
    cutoff = (date.today() - timedelta(days=threshold)).isoformat()
    today_iso = date.today().isoformat()

    last_reminded = {r["SaleID"]: r["LastSentAt"] for r in db.query(
        "SELECT SaleID, MAX(SentAt) AS LastSentAt FROM ARReminderLog WHERE SaleID IS NOT NULL GROUP BY SaleID")}

    customer_due = {}
    for r in invoice_rows:
        r["TelLink"] = _ar_phone_e164(r["Phone"])
        last_sent = last_reminded.get(r["SaleID"])
        if r["AgeDays"] > daily_after:
            due = r["AgeDays"] >= threshold and (last_sent is None or last_sent[:10] < today_iso)
        else:
            due = r["AgeDays"] >= threshold and (last_sent is None or last_sent < cutoff)
        r["ReminderDue"] = due
        r["ReminderMessage"] = _ar_reminder_message(r["CustomerName"], r["Due"], r["InvoiceNumber"], r["AgeDays"])
        if due:
            customer_due[r["CustomerID"]] = True
        else:
            customer_due.setdefault(r["CustomerID"], False)

    for c in customer_summary:
        c["TelLink"] = _ar_phone_e164(c["Phone"])
        c["ReminderDue"] = customer_due.get(c["CustomerID"], False)
        c["ReminderMessage"] = _ar_reminder_message(c["CustomerName"], c["due"])

    return threshold


@bp.route("/reports/accounts-receivable/remind", methods=["POST"])
def ar_remind():
    """Fire-and-forget logging call from the Call/WhatsApp buttons - the browser has already
    (or is about to) navigate to tel:/wa.me on its own; this just records that it happened, for
    the 'Reminder due' flag and an audit trail. Never blocks or redirects the actual call/chat.

    An account-level reminder (Customer Summary tab - no sale_id passed) logs one row per
    invoice currently contributing to that customer's due balance, not a single account-wide
    entry - its one WhatsApp/Call message covers the whole balance, so it counts as reminding
    every invoice within it, keeping the Invoice-wise tab's own per-invoice flags in sync with
    whatever was just actioned here (see get_ar_reminder_flags())."""
    user = get_current_user()
    customer_id = request.form.get("customer_id", type=int)
    sale_id = request.form.get("sale_id", type=int)  # optional - absent for an account-level reminder
    channel = request.form.get("channel")
    if not customer_id or channel not in ("Call", "WhatsApp"):
        return jsonify({"ok": False}), 400

    if sale_id:
        sale_ids = [sale_id]
    else:
        sale_ids = [r["SaleID"] for r in db.query(
            """SELECT SaleID FROM Sales WHERE CustomerID=? AND Status='Completed'
               AND (TaxableAmount - AmountReceived) > 0.005""", (customer_id,))] or [None]

    with db.transaction():
        for sid in sale_ids:
            db.execute("""INSERT INTO ARReminderLog (CustomerID, SaleID, Channel, SentByUserID, SentByUsername)
                        VALUES (?,?,?,?,?)""",
                       (customer_id, sid, channel, user["UserID"] if user else None,
                        user["Username"] if user else None))
    return jsonify({"ok": True})


@bp.route("/reports/accounts-receivable/invoices/pdf")
def accounts_receivable_invoices_pdf():
    """PDF export, two sections: a Customer-level summary first (alphabetical
    by customer name), then Invoice-level detail (also alphabetical by
    customer name) on its own page(s) after. Salesperson/Due Date are
    deliberately left out of the invoice table - not needed for this export.
    Same access control as the report itself, since this path starts with
    /reports/accounts-receivable too (see TAB_PATH_RULES)."""
    base_rows = get_ar_base_rows()
    customer_rows = get_ar_customer_summary(base_rows, sort="name")
    invoice_rows = sorted(get_ar_invoice_rows(base_rows), key=lambda r: (r["CustomerName"] or "").lower())

    customer_pdf_rows = [_ar_customer_pdf_row(c) for c in customer_rows]
    invoice_pdf_rows = [_ar_invoice_pdf_row(r) for r in invoice_rows]

    buf = rows_to_pdf_sections([
        (f"Customer-wise Summary ({len(customer_pdf_rows)})", customer_pdf_rows),
        (f"Invoice-wise Detail ({len(invoice_pdf_rows)})", invoice_pdf_rows),
    ], "Accounts Receivable — Due Details")
    from flask import send_file
    return send_file(buf, as_attachment=True,
                      download_name=f"Accounts_Receivable_Invoice_Wise_{today_str()}.pdf",
                      mimetype="application/pdf")


@bp.route("/reports/accounts-receivable")
def accounts_receivable():
    """Who owes the business how much, in five views (?view=summary|aging|invoices|zone|salesperson):
      - summary: one row per customer - invoiced/received/due, all-time (not date-scoped -
        dues don't expire at a month boundary the way a P&L period does).
      - aging: same due amount per customer, split into 0-30/31-60/61-90/90+ day buckets by
        PaymentDueDate (falling back to SaleDate when no due date was set).
      - invoices: every individual unpaid/partially-paid invoice with its own due amount.
      - zone: dues rolled up by Customer.Zone, with every customer in that zone listed underneath
        and a zone subtotal (a drill-down, not just a zone-level total row).
      - salesperson: dues rolled up by the salesperson (Employee) on the sale.
    Only Completed sales count (Draft isn't billed yet, Cancelled never was).

    Due is TaxableAmount - AmountReceived (the product value only), not TotalAmount (which
    includes GST) - AmountReceived. This matches how Balance Due already works on the Sale
    form's Reverse Charge calculation: GST collected on an invoice isn't money the business
    keeps as revenue owed to it, so it doesn't belong in what's chased as "due" here."""
    view = request.args.get("view", "summary")
    today_iso = date.today().isoformat()
    pay_date_from = (request.args.get("from") or "").strip()
    pay_date_to = (request.args.get("to") or "").strip()
    q = (request.args.get("q") or "").strip()
    ql = q.lower()

    payment_rows = []
    payment_totals = {"count": 0, "cash": 0, "bank": 0, "total": 0}
    if view == "payments":
        # Two sources feed this list, combined into one chronological feed:
        #  1. SalePayments - every payment logged via the "Record Payment" button
        #     (added in this app on 17 Sep 2026), dated when it was actually
        #     collected/recorded.
        #  2. Every Sale's own CashAmount/BankAmount - the lump-sum split entered
        #     directly on the Sale form at save time (create_sale()/sale_edit() set
        #     these to add up to whatever AmountReceived was typed in). This is the
        #     ONLY record of anything collected before the Record Payment feature
        #     existed, so without folding it in here, every sale recorded prior to
        #     that date would be invisible on this tab even though the money was
        #     genuinely received - shown dated to the Sale's own date, with a note
        #     explaining it predates per-payment logging.
        # A sale can legitimately have both: its original at-sale-time collection
        # (source 2) plus a later top-up logged afterward via Record Payment
        # (source 1) - both show up as their own separate dated rows.
        entries = []
        sale_payment_rows = db.query("""
            SELECT sp.PaymentID, sp.PaymentDate, sp.Amount, sp.PaymentMethod, sp.Notes,
                   s.SaleID, s.InvoiceNumber, c.CustomerName
            FROM SalePayments sp
            JOIN Sales s ON s.SaleID = sp.SaleID
            JOIN Customers c ON c.CustomerID = s.CustomerID
            WHERE s.Status <> 'Cancelled'
        """)
        for r in sale_payment_rows:
            method = r["PaymentMethod"] or "Cash"
            cash_portion = r["Amount"] if method == "Cash" else 0
            bank_portion = 0 if method == "Cash" else r["Amount"]
            entries.append({
                "PaymentDate": r["PaymentDate"], "CustomerName": r["CustomerName"],
                "InvoiceNumber": r["InvoiceNumber"], "SaleID": r["SaleID"], "PaymentMethod": method,
                "Amount": r["Amount"], "CashPortion": cash_portion, "BankPortion": bank_portion,
                "Notes": r["Notes"] or "", "sort2": r["PaymentID"],
            })

        baseline_rows = db.query("""
            SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.CashAmount, s.BankAmount, c.CustomerName
            FROM Sales s JOIN Customers c ON c.CustomerID = s.CustomerID
            WHERE s.Status <> 'Cancelled' AND (COALESCE(s.CashAmount,0) + COALESCE(s.BankAmount,0)) > 0.005
        """)
        for r in baseline_rows:
            cash, bank = round(r["CashAmount"] or 0, 2), round(r["BankAmount"] or 0, 2)
            method = "Mixed" if (cash > 0.005 and bank > 0.005) else ("Bank" if bank > 0.005 else "Cash")
            entries.append({
                "PaymentDate": r["SaleDate"], "CustomerName": r["CustomerName"],
                "InvoiceNumber": r["InvoiceNumber"], "SaleID": r["SaleID"], "PaymentMethod": method,
                "Amount": round(cash + bank, 2), "CashPortion": cash, "BankPortion": bank,
                "Notes": "Recorded with the sale (before per-payment logging)", "sort2": -1,
            })

        if pay_date_from:
            entries = [e for e in entries if e["PaymentDate"] >= pay_date_from]
        if pay_date_to:
            entries = [e for e in entries if e["PaymentDate"] <= pay_date_to]
        if ql:
            entries = [e for e in entries if ql in (e["CustomerName"] or "").lower()
                       or ql in (e["InvoiceNumber"] or "").lower()
                       or ql in (e["Notes"] or "").lower()
                       or ql in (e["PaymentMethod"] or "").lower()]
        entries.sort(key=lambda e: (e["PaymentDate"], e["SaleID"], e["sort2"]), reverse=True)

        payment_rows = entries
        payment_totals = {
            "count": len(payment_rows),
            "cash": round(sum(r["CashPortion"] for r in payment_rows), 2),
            "bank": round(sum(r["BankPortion"] for r in payment_rows), 2),
            "total": round(sum(r["Amount"] for r in payment_rows), 2),
        }

    base_rows = get_ar_base_rows()
    if ql:
        base_rows = [r for r in base_rows if ql in (r["CustomerName"] or "").lower()
                     or ql in (r["Phone"] or "").lower()
                     or ql in (r["Zone"] or "").lower()
                     or ql in (r["InvoiceNumber"] or "").lower()
                     or ql in (r["EmployeeName"] or "").lower()
                     or ql in (r["AccountNumber"] or "").lower()]

    total_due = round(sum(r["Due"] for r in base_rows), 2)
    customer_count = len({r["CustomerID"] for r in base_rows})

    customer_summary = get_ar_customer_summary(base_rows)

    age_days = ar_age_days  # module-level now, shared with the PDF export

    def bucket_for(days):
        if days <= 30:
            return "b0_30"
        if days <= 60:
            return "b31_60"
        if days <= 90:
            return "b61_90"
        return "b90_plus"

    aging_by_customer = {}
    for r in base_rows:
        agg = aging_by_customer.setdefault(r["CustomerID"], {
            "CustomerID": r["CustomerID"], "CustomerName": r["CustomerName"], "Zone": r["Zone"],
            "b0_30": 0, "b31_60": 0, "b61_90": 0, "b90_plus": 0, "total": 0,
        })
        d = r["PaymentDueDate"] or r["SaleDate"]
        agg[bucket_for(age_days(d))] += r["Due"]
        agg["total"] += r["Due"]
    aging_rows = sorted(aging_by_customer.values(), key=lambda a: -a["total"])
    for a in aging_rows:
        for k in ("b0_30", "b31_60", "b61_90", "b90_plus", "total"):
            a[k] = round(a[k], 2)
    aging_totals = {k: round(sum(a[k] for a in aging_rows), 2) for k in ("b0_30", "b31_60", "b61_90", "b90_plus", "total")}

    salesperson_agg = {}
    for r in base_rows:
        sk = r["EmployeeName"] or "(Counter / Unassigned)"
        s = salesperson_agg.setdefault(sk, {"label": sk, "due": 0, "invoice_count": 0})
        s["due"] += r["Due"]
        s["invoice_count"] += 1
    salesperson_rows = sorted(salesperson_agg.values(), key=lambda a: -a["due"])
    for a in salesperson_rows:
        a["due"] = round(a["due"], 2)

    # Zone-wise: every zone, with the customers who owe money in that zone listed underneath
    # and a zone subtotal - a drill-down, not just a single rolled-up total row per zone.
    zone_groups = {}
    for a in customer_summary:
        zk = a["Zone"] or "(No Zone)"
        zone_groups.setdefault(zk, {"zone": zk, "customers": [], "due": 0, "invoice_count": 0})
    for a in customer_summary:
        zk = a["Zone"] or "(No Zone)"
        g = zone_groups[zk]
        g["customers"].append(a)
        g["due"] += a["due"]
        g["invoice_count"] += a["invoice_count"]
    zone_wise = sorted(zone_groups.values(), key=lambda g: -g["due"])
    for g in zone_wise:
        g["due"] = round(g["due"], 2)
        g["customers"].sort(key=lambda a: -a["due"])

    invoice_rows = [dict(r) for r in base_rows]
    for r in invoice_rows:
        r["AgeDays"] = age_days(r["PaymentDueDate"] or r["SaleDate"])
        r["Due"] = round(r["Due"], 2)

    ar_reminder_overdue_days = get_ar_reminder_flags(invoice_rows, customer_summary)
    # Distinct customers, not invoice count - one customer with 3 overdue invoices is one
    # reminder to make, not three. Computed from invoice_rows (not customer_summary) regardless
    # of which tab is open, so this stat card means the same thing on every view.
    reminders_due_count = len({r["CustomerID"] for r in invoice_rows if r["ReminderDue"]})

    return render_template("accounts_receivable.html", view=view, today=today_iso,
                            total_due=total_due, customer_count=customer_count,
                            customer_summary=customer_summary, aging_rows=aging_rows,
                            aging_totals=aging_totals, invoice_rows=invoice_rows,
                            zone_wise=zone_wise, salesperson_rows=salesperson_rows,
                            payment_rows=payment_rows, payment_totals=payment_totals,
                            pay_date_from=pay_date_from, pay_date_to=pay_date_to, q=q,
                            ar_reminder_overdue_days=ar_reminder_overdue_days,
                            reminders_due_count=reminders_due_count)


@bp.route("/reports/pnl")
def pnl_report():
    today = date.today()
    default_from = today.replace(day=1).isoformat()
    default_to = today.isoformat()
    date_from = request.args.get("from") or default_from
    date_to = request.args.get("to") or default_to

    revenue_row = db.query("""SELECT COALESCE(SUM(TaxableAmount), 0) AS total FROM Sales
                            WHERE Status='Completed' AND SaleDate BETWEEN ? AND ?""",
                           (date_from, date_to), one=True)
    revenue = revenue_row["total"]

    cogs_row = db.query("""SELECT COALESCE(SUM(sl.Qty * pr.CostPrice), 0) AS total
                         FROM SalesLines sl
                         JOIN Sales s ON s.SaleID = sl.SaleID
                         JOIN Products pr ON pr.ProductID = sl.ProductID
                         WHERE s.Status='Completed' AND s.SaleDate BETWEEN ? AND ?""",
                        (date_from, date_to), one=True)
    cogs = cogs_row["total"]

    expense_rows = db.query("""SELECT Category, COALESCE(SUM(Amount), 0) AS total FROM Expenses
                             WHERE ExpenseDate BETWEEN ? AND ? GROUP BY Category ORDER BY total DESC""",
                            (date_from, date_to))
    total_expenses = sum(r["total"] for r in expense_rows)

    salary_row = db.query("""SELECT COALESCE(SUM(NetPayable), 0) AS total FROM SalaryPayments
                           WHERE Status='Paid' AND PaymentDate BETWEEN ? AND ?""",
                          (date_from, date_to), one=True)
    total_salary = salary_row["total"]

    incentive_row = db.query("""SELECT COALESCE(SUM(sl.Qty * pr.IncentivePerUnit), 0) AS total
                              FROM SalesLines sl
                              JOIN Sales s ON s.SaleID = sl.SaleID
                              JOIN Products pr ON pr.ProductID = sl.ProductID
                              WHERE s.Status='Completed' AND s.SaleDate BETWEEN ? AND ?""",
                             (date_from, date_to), one=True)
    incentive_income = round(incentive_row["total"], 2)

    # Vehicle maintenance & repairs (Fleet > Maintenance) - revenue expenditure,
    # fully deductible in the period incurred (Section 37(1)), so it's summed
    # straight from the maintenance log rather than requiring a duplicate
    # manual entry under Operating Expenses.
    maint_row = db.query("""SELECT COALESCE(SUM(Cost), 0) AS total FROM VehicleMaintenance
                          WHERE ServiceDate BETWEEN ? AND ?""", (date_from, date_to), one=True)
    vehicle_maintenance_total = round(maint_row["total"], 2)

    # Vehicle depreciation - capital expenditure, written down under the
    # Income Tax Act's 15% WDV method (see depreciation.py for the full
    # rationale) rather than expensed in full on purchase.
    d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
    d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
    vehicles_for_dep = db.query("SELECT PurchaseDate, PurchasePrice FROM Vehicles WHERE PurchaseDate IS NOT NULL AND PurchasePrice IS NOT NULL")
    vehicle_depreciation_total = 0.0
    for v in vehicles_for_dep:
        try:
            p_date = datetime.strptime(v["PurchaseDate"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        vehicle_depreciation_total += depreciation.vehicle_depreciation_for_range(
            p_date, v["PurchasePrice"], d_from, d_to)
    vehicle_depreciation_total = round(vehicle_depreciation_total, 2)

    gross_profit = round(revenue - cogs, 2)
    total_operating_costs = round(total_expenses + total_salary + vehicle_maintenance_total
                                   + vehicle_depreciation_total, 2)
    net_profit = round(gross_profit + incentive_income - total_operating_costs, 2)

    return render_template("pnl_report.html", date_from=date_from, date_to=date_to,
                            revenue=round(revenue, 2), cogs=round(cogs, 2), gross_profit=gross_profit,
                            incentive_income=incentive_income,
                            expense_rows=expense_rows, total_expenses=round(total_expenses, 2),
                            total_salary=round(total_salary, 2),
                            vehicle_maintenance_total=vehicle_maintenance_total,
                            vehicle_depreciation_total=vehicle_depreciation_total,
                            total_operating_costs=total_operating_costs,
                            net_profit=net_profit)


@bp.route("/gst")
def gst_dashboard():
    company = get_company_settings()
    due_items = gst_logic.gst_due_dates(company)
    today = date.today()
    recent_uploads = db.query("SELECT * FROM Gstr2bUploads ORDER BY UploadID DESC LIMIT 5")
    return render_template("gst_dashboard.html", company=company, due_items=due_items, today=today,
                            recent_uploads=recent_uploads)


@bp.route("/gst/gstr1")
def gst_gstr1():
    year, month = _year_month_from_request()
    summary = gst_logic.gstr1_summary(year, month)
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("gst_gstr1.html", summary=summary, year=year, month=month,
                            month_name=MONTH_NAMES[month],
                            prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year)


@bp.route("/gst/gstr3b")
def gst_gstr3b():
    year, month = _year_month_from_request()
    company = get_company_settings()
    summary = gst_logic.gstr3b_summary(year, month)
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("gst_gstr3b.html", summary=summary, year=year, month=month,
                            month_name=MONTH_NAMES[month], company=company,
                            prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year)


@bp.route("/gst/gstr2b")
def gst_gstr2b_list():
    uploads = db.query("SELECT * FROM Gstr2bUploads ORDER BY Period DESC, UploadID DESC")
    return render_template("gst_gstr2b.html", uploads=uploads, today=today_str())


@bp.route("/gst/gstr2b/upload", methods=["POST"])
def gst_gstr2b_upload():
    period = (request.form.get("period") or "").strip()
    file_storage = request.files.get("file")
    if not period:
        flash("Select which month (return period) this GSTR-2B file is for.", "error")
        return redirect(url_for("gst_gstr2b_list"))
    if not file_storage or not file_storage.filename:
        flash("Choose a GSTR-2B Excel (.xlsx) file to upload.", "error")
        return redirect(url_for("gst_gstr2b_list"))

    folder = os.path.join(UPLOAD_ROOT, GSTR2B_UPLOAD_DIR, period)
    os.makedirs(folder, exist_ok=True)
    safe_name = secure_filename(file_storage.filename) or f"gstr2b-{uuid.uuid4().hex[:8]}.xlsx"
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    full_path = os.path.join(folder, stored_name)
    file_storage.save(full_path)
    rel_path = "/".join([GSTR2B_UPLOAD_DIR, period, stored_name])

    result = gst_logic.parse_gstr2b_excel(full_path)
    upload_id = db.execute("""INSERT INTO Gstr2bUploads (Period, FileName, StoredPath, UploadedAt, ParseStatus,
                ParseMessage, ITCIntegrated, ITCCentral, ITCState, ITCCess, RecordCount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
               (period, safe_name, rel_path, datetime.now().isoformat(timespec="seconds"),
                "Parsed" if result["ok"] else "Failed", result["message"],
                result["itc_integrated"], result["itc_central"], result["itc_state"], result["itc_cess"],
                result["record_count"]))
    flash(result["message"], "success" if result["ok"] else "error")
    return redirect(url_for("gst_gstr2b_list"))


@bp.route("/gst/gstr2b/<int:upload_id>/download")
def gst_gstr2b_download(upload_id):
    upload = db.query("SELECT * FROM Gstr2bUploads WHERE UploadID=?", (upload_id,), one=True)
    if not upload:
        flash("File not found.", "error")
        return redirect(url_for("gst_gstr2b_list"))
    full_path = os.path.join(UPLOAD_ROOT, upload["StoredPath"])
    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    return send_from_directory(directory, filename, as_attachment=True, download_name=upload["FileName"])


@bp.route("/gst/gstr2b/<int:upload_id>/delete", methods=["POST"])
def gst_gstr2b_delete(upload_id):
    upload = db.query("SELECT * FROM Gstr2bUploads WHERE UploadID=?", (upload_id,), one=True)
    if upload:
        full_path = os.path.join(UPLOAD_ROOT, upload["StoredPath"])
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass
        db.execute("DELETE FROM Gstr2bUploads WHERE UploadID=?", (upload_id,))
        flash(f"GSTR-2B upload for {upload['Period']} removed.", "success")
    return redirect(url_for("gst_gstr2b_list"))
