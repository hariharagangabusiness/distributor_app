"""inventory routes."""
import db
from core.auth import admin_required, get_current_user
from core.helpers import create_sale, direct_sales_today_for_employee, get_company_settings, get_custom_attachments, get_custom_field_defs, get_custom_values, get_effective_columns, get_employee_month_target_progress, get_products_with_stock, log_stock_issue_change, next_stock_issue_number, save_custom_fields, stock_issue_post_line, today_str, true_product_qty_sold
from datetime import datetime
from flask import flash, redirect, render_template, request, url_for



from flask import Blueprint

bp = Blueprint("inventory", __name__)


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Inventory / Products
# ---------------------------------------------------------------------


@bp.route("/inventory")
def inventory_list():
    q = request.args.get("q", "").strip()
    filt = request.args.get("filter", "")
    products = get_products_with_stock()
    if q:
        ql = q.lower()
        products = [p for p in products if ql in p["ProductName"].lower() or (p["SKU"] or "").lower().find(ql) >= 0]
    if filt == "low":
        products = [p for p in products if p["CurrentStock"] <= p["MinStock"]]
    elif filt == "over":
        products = [p for p in products if p["MaxStock"] > 0 and p["CurrentStock"] >= p["MaxStock"]]
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    columns = get_effective_columns("Product")
    pending_review_count = db.query(
        "SELECT COUNT(*) c FROM DirectSaleReviews WHERE Status='Pending'", one=True)["c"]
    return render_template("inventory_list.html", products=products, q=q, filt=filt, suppliers=suppliers,
                            columns=columns, pending_review_count=pending_review_count)


@bp.route("/inventory/new", methods=["GET", "POST"])
@bp.route("/inventory/<int:pid>/edit", methods=["GET", "POST"])
def inventory_form(pid=None):
    product = None
    if pid:
        product = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if request.method == "POST":
        f = request.form
        args = (f["sku"], f["product_name"], f["category"], f["unit"],
                float(f["cost_price"] or 0), float(f["selling_price"] or 0),
                float(f["min_stock"] or 0), float(f["max_stock"] or 0),
                float(f["reorder_qty"] or 0),
                int(f["default_supplier_id"]) if f.get("default_supplier_id") else None,
                f.get("hsn_code", ""), float(f.get("gst_rate") or 0),
                1 if f.get("active") else 0,
                f.get("scheme_name", ""), float(f.get("scheme_percent") or 0),
                float(f.get("incentive_per_unit") or 0))
        try:
            if pid:
                db.execute("""UPDATE Products SET SKU=?, ProductName=?, Category=?, Unit=?, CostPrice=?,
                            SellingPrice=?, MinStock=?, MaxStock=?, ReorderQty=?, DefaultSupplierID=?,
                            HSNCode=?, GSTRate=?, Active=?, SchemeName=?, SchemePercent=?, IncentivePerUnit=?
                            WHERE ProductID=?""", args + (pid,))
                save_custom_fields("Product", pid, f)
                flash(f"Product '{f['product_name']}' updated.", "success")
            else:
                new_id = db.execute("""INSERT INTO Products (SKU, ProductName, Category, Unit, CostPrice,
                            SellingPrice, MinStock, MaxStock, ReorderQty, DefaultSupplierID, HSNCode, GSTRate, Active,
                            SchemeName, SchemePercent, IncentivePerUnit)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", args)
                opening = float(f.get("opening_stock") or 0)
                if opening:
                    db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                                QtyChange, RefType, Notes) VALUES (?,?,?,?,?,?)""",
                               (new_id, today_str(), "Opening Stock", opening, "Manual", "Opening balance"))
                save_custom_fields("Product", new_id, f)
                flash(f"Product '{f['product_name']}' added.", "success")
        except Exception:
            flash(f"Could not save — SKU '{f['sku']}' is already used by another product. "
                  f"Pick a different SKU and try again.", "error")
            return redirect(url_for("inventory_form", pid=pid) if pid else url_for("inventory_form"))
        return redirect(url_for("inventory_list"))
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    custom_fields = get_custom_field_defs("Product")
    custom_values = get_custom_values("Product", pid) if pid else {}
    return render_template("inventory_form.html", product=product, suppliers=suppliers,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=pid, custom_attachments=get_custom_attachments("Product", pid))


@bp.route("/inventory/<int:pid>/adjust", methods=["GET", "POST"])
def inventory_adjust(pid):
    product = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if request.method == "POST":
        f = request.form
        direction = f["direction"]
        qty = abs(float(f["qty"]))
        change = qty if direction == "in" else -qty
        db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                    QtyChange, RefType, Notes) VALUES (?,?,?,?,?,?)""",
                   (pid, f["adjust_date"] or today_str(), f"Adjustment-{'In' if direction=='in' else 'Out'}",
                    change, "Manual", f.get("notes", "")))
        flash("Stock adjustment recorded.", "success")
        return redirect(url_for("inventory_list"))
    history = db.query("""SELECT * FROM InventoryTransactions WHERE ProductID=?
                        ORDER BY TransactionDate DESC, TransactionID DESC LIMIT 30""", (pid,))
    return render_template("stock_adjust.html", product=product, history=history, today=today_str())


# ---------------------------------------------------------------------
# Inventory Reconciliation: shows exactly why a product's current stock
# (the InventoryTransactions ledger total) is what it is, and — for
# products issued out to salespeople via Stock Issues — how much of the
# stock that left the warehouse is still genuinely unaccounted for versus
# just pending an end-of-day reconciliation. Uses the same ground-truth
# true_product_qty_sold() logic as Live Sales Monitor/Reconcile rather than
# trusting StockIssueLines.QtySold directly, since older reconciled issues
# (reconciled before that fix existed) can have an understated QtySold on
# file even though the actual stock genuinely was sold - this report would
# otherwise flag that gap as "missing" stock when it's really just a stale
# recorded figure.
# ---------------------------------------------------------------------


def _true_qty_sold_lookup():
    """{(EmployeeID, SaleDate, ProductID): total qty}, computed straight from
    SalesLines/Sales - the same ground truth true_product_qty_sold() uses,
    but as one grouped query instead of one query per Stock Issue line."""
    rows = db.query("""SELECT s.EmployeeID, s.SaleDate, sl.ProductID, SUM(sl.Qty) AS q
                     FROM SalesLines sl JOIN Sales s ON s.SaleID = sl.SaleID
                     WHERE s.Status <> 'Cancelled'
                     GROUP BY s.EmployeeID, s.SaleDate, sl.ProductID""")
    return {(r["EmployeeID"], r["SaleDate"], r["ProductID"]): (r["q"] or 0) for r in rows}


def _unassigned_sold_by_issue():
    """{(IssueID, ProductID): qty} - how much of a Stock Issue line was billed on that
    issue's own auto-created 'Unassigned' invoice (StockIssues.SaleID), still sitting
    unreassigned. That invoice is deliberately created with NO EmployeeID (see
    stock_issue_reconcile()), so _true_qty_sold_lookup() above - keyed by EmployeeID -
    can never see it. Without this, the Reconciliation report's day-by-day Unaccounted
    figure wildly overstates the real gap for any day whose sales were never
    individually reassigned to real customers (the report showed a product's true
    all-time Gap as 72 while the day-by-day breakdown summed to 275, entirely because
    of this blind spot - see phase47 follow-up). Once a Sale is fully reassigned away
    (sale_reassign()), StockIssues.SaleID is cleared and this can no longer trace it -
    a known limitation, not fixed here."""
    rows = db.query("""SELECT si.IssueID, sl.ProductID, SUM(sl.Qty) AS q
                     FROM StockIssues si
                     JOIN Sales s ON s.SaleID = si.SaleID
                     JOIN SalesLines sl ON sl.SaleID = s.SaleID
                     WHERE si.SaleID IS NOT NULL AND s.Status <> 'Cancelled'
                     GROUP BY si.IssueID, sl.ProductID""")
    return {(r["IssueID"], r["ProductID"]): (r["q"] or 0) for r in rows}


def _direct_sale_qty_lookup():
    """{(EmployeeID, SaleDate, ProductID): qty} - how much of a salesperson's total
    units sold that date/product was actually a genuine Direct Sale (a real, separate
    warehouse deduction - TransactionType='Sale' in InventoryTransactions), rather than
    credited against a Stock Issue line. _true_qty_sold_lookup() sums ALL SalesLines for
    an (employee, date, product) regardless of channel, so on any day a salesperson had
    BOTH stock credited from their issue AND a separate genuine Direct Sale (e.g. an
    oversell beyond that day's issued capacity), the Direct Sale portion was being
    counted twice: once correctly as 'Direct Sale' on the ledger, and again as if it
    were sold from the issue's own stock, understating that line's Unaccounted. This
    lookup lets that portion be subtracted back out before comparing against a Stock
    Issue line's Qty Issued."""
    rows = db.query("""SELECT s.EmployeeID, s.SaleDate, it.ProductID, SUM(-it.QtyChange) AS q
                     FROM InventoryTransactions it
                     JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
                     WHERE it.TransactionType = 'Sale' AND s.Status <> 'Cancelled'
                     GROUP BY s.EmployeeID, s.SaleDate, it.ProductID""")
    return {(r["EmployeeID"], r["SaleDate"], r["ProductID"]): (r["q"] or 0) for r in rows}


def _inventory_reconciliation_summary():
    """Per-product breakdown: every InventoryTransactions bucket that sums to the
    current ledger stock, plus the true-ground-truth Unaccounted (stock issued to
    a salesperson on an already-Reconciled day that was never sold/returned/given
    free) and Pending (issued but the day hasn't been reconciled yet, so nothing
    is wrong - it's just still out) quantities."""
    ledger_rows = db.query("""SELECT ProductID, TransactionType, SUM(QtyChange) AS amt
                            FROM InventoryTransactions GROUP BY ProductID, TransactionType""")
    ledger = {}
    for r in ledger_rows:
        ledger.setdefault(r["ProductID"], {})[r["TransactionType"]] = r["amt"] or 0

    issue_lines = db.query("""SELECT sil.IssueID, sil.ProductID, sil.QtyIssued, sil.QtyReturned, sil.QtyFree,
                            si.EmployeeID, si.IssueDate, si.Status
                            FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID""")
    true_sold = _true_qty_sold_lookup()
    unassigned_sold = _unassigned_sold_by_issue()
    direct_sold = _direct_sale_qty_lookup()

    unaccounted = {}
    pending = {}
    for line in issue_lines:
        pid = line["ProductID"]
        if line["Status"] == "Reconciled":
            # Ground-truth sold FROM THIS ISSUE for this line = everything billed to the
            # salesperson's own named customers that day, PLUS whatever's still billed on
            # this issue's own auto-created "Unassigned" invoice (not yet reassigned) -
            # see _unassigned_sold_by_issue() - MINUS whatever of that total was actually a
            # genuine, separate Direct Sale warehouse deduction rather than stock credited
            # from this issue - see _direct_sale_qty_lookup() (otherwise a same-day Direct
            # Sale gets silently double-counted as if it also came from the issue).
            key = (line["EmployeeID"], line["IssueDate"], pid)
            sold = (true_sold.get(key, 0) + unassigned_sold.get((line["IssueID"], pid), 0)
                    - direct_sold.get(key, 0))
            gap = round((line["QtyIssued"] or 0) - sold - (line["QtyReturned"] or 0) - (line["QtyFree"] or 0), 2)
            unaccounted[pid] = round(unaccounted.get(pid, 0) + gap, 2)
        else:
            pending[pid] = round(pending.get(pid, 0) + (line["QtyIssued"] or 0), 2)

    products = db.query("SELECT ProductID, ProductName, SKU, Unit FROM Products ORDER BY ProductName")
    out = []
    for p in products:
        pid = p["ProductID"]
        buckets = ledger.get(pid, {})
        ledger_stock = round(sum(buckets.values()), 2)
        row = dict(p)
        row.update(
            opening=buckets.get("Opening Stock", 0), purchases=buckets.get("Purchase", 0),
            issued=buckets.get("Issue", 0), returned_in=buckets.get("Return-In", 0),
            free_scheme=buckets.get("Free Scheme", 0), direct_sale=buckets.get("Sale", 0),
            adjustment_in=buckets.get("Adjustment-In", 0), adjustment_out=buckets.get("Adjustment-Out", 0),
            ledger_stock=ledger_stock,
            unaccounted=unaccounted.get(pid, 0), pending_reconciliation=pending.get(pid, 0),
        )
        out.append(row)
    return out


@bp.route("/inventory/reconciliation")
@admin_required
def inventory_reconciliation_summary():
    rows = _inventory_reconciliation_summary()
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [r for r in rows if q in r["ProductName"].lower() or q in (r["SKU"] or "").lower()]
    only_gaps = request.args.get("only_gaps") == "1"
    if only_gaps:
        rows = [r for r in rows if abs(r["unaccounted"]) > 0.01]
    rows.sort(key=lambda r: abs(r["unaccounted"]), reverse=True)
    return render_template("inventory_reconciliation.html", rows=rows, q=request.args.get("q", ""),
                            only_gaps=only_gaps)


@bp.route("/inventory/<int:pid>/reconciliation")
@admin_required
def inventory_reconciliation_detail(pid):
    product = db.query("SELECT * FROM Products WHERE ProductID=?", (pid,), one=True)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("inventory_reconciliation_summary"))

    ledger = db.query("""SELECT * FROM InventoryTransactions WHERE ProductID=?
                       ORDER BY TransactionDate, TransactionID""", (pid,))
    running = 0
    ledger_rows = []
    for t in ledger:
        running = round(running + (t["QtyChange"] or 0), 2)
        row = dict(t)
        row["running_balance"] = running
        ledger_rows.append(row)
    ledger_rows.reverse()  # most recent first, like every other list page in the app

    issue_lines = db.query("""SELECT sil.*, si.EmployeeID, si.IssueDate, si.Status, e.EmployeeName
                            FROM StockIssueLines sil
                            JOIN StockIssues si ON si.IssueID = sil.IssueID
                            JOIN Employees e ON e.EmployeeID = si.EmployeeID
                            WHERE sil.ProductID=? ORDER BY si.IssueDate DESC, si.IssueID DESC""", (pid,))
    true_sold = _true_qty_sold_lookup()
    unassigned_sold = _unassigned_sold_by_issue()
    direct_sold = _direct_sale_qty_lookup()
    issue_breakdown = []
    total_unaccounted = 0
    total_pending = 0
    for line in issue_lines:
        row = dict(line)
        if line["Status"] == "Reconciled":
            # Ground-truth sold FROM THIS ISSUE for this line = everything billed to the
            # salesperson's own named customers that day, PLUS whatever's still billed on
            # this issue's own auto-created "Unassigned" invoice (not yet reassigned) -
            # see _unassigned_sold_by_issue() - MINUS whatever of that total was actually a
            # genuine, separate Direct Sale warehouse deduction rather than stock credited
            # from this issue - see _direct_sale_qty_lookup().
            key = (line["EmployeeID"], line["IssueDate"], pid)
            true_qty = (true_sold.get(key, 0) + unassigned_sold.get((line["IssueID"], pid), 0)
                        - direct_sold.get(key, 0))
            row["true_qty_sold"] = true_qty
            row["unaccounted"] = round((line["QtyIssued"] or 0) - true_qty
                                        - (line["QtyReturned"] or 0) - (line["QtyFree"] or 0), 2)
            total_unaccounted = round(total_unaccounted + row["unaccounted"], 2)
        else:
            row["true_qty_sold"] = None
            row["unaccounted"] = None
            total_pending = round(total_pending + (line["QtyIssued"] or 0), 2)
        issue_breakdown.append(row)

    buckets = {}
    for t in ledger:
        buckets[t["TransactionType"]] = round(buckets.get(t["TransactionType"], 0) + (t["QtyChange"] or 0), 2)
    ledger_stock = round(sum(buckets.values()), 2)

    # "Direct Sale" ledger rows (TransactionType='Sale') are posted whenever a Sales-tab
    # entry's quantity for this product couldn't be fully covered by crediting the
    # salesperson's own open Stock Issue line for that day - e.g. the day's issued stock
    # for this product had already run out, the sale has no salesperson/Stock-Issue link
    # at all (an office/counter sale), or it's the catch-all "Unassigned" sale auto-created
    # at Reconcile time for whatever a Stock Issue line's true sold total exceeds. Every one
    # of these deducts straight from warehouse stock rather than from stock already earmarked
    # to a van, so they're broken out here (joined back to the actual Sale/Customer/Employee)
    # instead of only showing as an anonymous "Sale" row in the raw ledger.
    direct_sale_rows = db.query("""
        SELECT it.TransactionID, it.TransactionDate, it.QtyChange, it.RefID AS SaleID, it.Notes AS InvoiceNumber,
               s.CustomerID, c.CustomerName, s.EmployeeID, e.EmployeeName, s.Status AS SaleStatus
        FROM InventoryTransactions it
        LEFT JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
        LEFT JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        WHERE it.ProductID=? AND it.TransactionType='Sale'
        ORDER BY it.TransactionDate DESC, it.TransactionID DESC
    """, (pid,))
    direct_sale_total = round(sum((r["QtyChange"] or 0) for r in direct_sale_rows), 2)

    # Total Units Sold (true, all-time) - straight from SalesLines/Sales, independent of any
    # StockIssueLines figure, so it's the actual ground truth regardless of source (whether the
    # sale was credited against an Issue or fell through to a Direct Sale). This is the number
    # to compare against "Issued to salespeople" + "Returned In" + "Given Free" below: if
    # (Issued - Returned - Free) is bigger than this total, the difference is genuinely
    # unaccounted-for stock - issued out (or otherwise leaving the warehouse) but never sold,
    # returned, or given away for free under a scheme.
    total_units_sold = db.query("""SELECT COALESCE(SUM(sl.Qty), 0) q FROM SalesLines sl
                                 JOIN Sales s ON s.SaleID = sl.SaleID
                                 WHERE sl.ProductID=? AND s.Status <> 'Cancelled'""", (pid,), one=True)["q"] or 0
    total_units_sold = round(total_units_sold, 2)
    # How much of Total Units Sold came from stock that was issued out to a salesperson,
    # rather than deducted straight from the warehouse as a Direct Sale (that portion, by
    # definition, was never part of what was issued in the first place, so it must be
    # excluded before comparing against the Issued/Returned/Free figures below).
    sold_via_issue = round(total_units_sold - abs(direct_sale_total), 2)
    # (Issue is stored negative, Return-In positive, Free Scheme negative - normalize all
    # three to "how much left the warehouse via Issue, net of what came back" for a clean
    # comparison: this SHOULD equal sold_via_issue if every issued unit is accounted for.)
    should_be_sold = round(-buckets.get("Issue", 0) - buckets.get("Return-In", 0) + (-buckets.get("Free Scheme", 0)), 2)
    sales_gap = round(should_be_sold - sold_via_issue, 2)

    return render_template("inventory_reconciliation_detail.html", product=product, ledger_rows=ledger_rows,
                            buckets=buckets, ledger_stock=ledger_stock, issue_breakdown=issue_breakdown,
                            total_unaccounted=total_unaccounted, total_pending=total_pending,
                            total_units_sold=total_units_sold, sold_via_issue=sold_via_issue,
                            should_be_sold=should_be_sold, sales_gap=sales_gap,
                            direct_sale_rows=direct_sale_rows, direct_sale_total=direct_sale_total)


# ---------------------------------------------------------------------
# Direct Sale Review queue - every "Sale"-type InventoryTransactions row
# (stock deducted straight from the warehouse, beyond what was issued to a
# salesperson) is parked here for an Admin to confirm it isn't a duplicate
# deduction. Stock is NOT held back for review - it deducts immediately as
# before; this is a non-blocking audit layer on top. See
# migrate_direct_sale_reviews.py for the backfill of historical rows.
# ---------------------------------------------------------------------


@bp.route("/inventory/direct-sale-review")
@admin_required
def direct_sale_review_list():
    status_filter = request.args.get("status", "Pending")
    q = (request.args.get("q") or "").strip()

    where = []
    params = []
    if status_filter in ("Pending", "Confirmed", "Flagged"):
        where.append("dsr.Status = ?")
        params.append(status_filter)
    if q:
        where.append("""(p.ProductName LIKE ? OR c.CustomerName LIKE ? OR e.EmployeeName LIKE ?
                       OR s.InvoiceNumber LIKE ?)""")
        like = f"%{q}%"
        params.extend([like, like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.query(f"""
        SELECT dsr.*, it.TransactionDate, it.QtyChange, it.RefID AS SaleID,
               p.ProductName, s.InvoiceNumber, s.SaleDate, s.EmployeeID, c.CustomerName,
               e.EmployeeName, si.IssueNumber AS AssignedIssueNumber
        FROM DirectSaleReviews dsr
        JOIN InventoryTransactions it ON it.TransactionID = dsr.TransactionID
        JOIN Products p ON p.ProductID = dsr.ProductID
        LEFT JOIN Sales s ON s.SaleID = it.RefID AND it.RefType = 'Sale'
        LEFT JOIN Customers c ON c.CustomerID = s.CustomerID
        LEFT JOIN Employees e ON e.EmployeeID = s.EmployeeID
        LEFT JOIN StockIssues si ON si.IssueID = dsr.StockIssueID
        {where_sql}
        ORDER BY dsr.CreatedAt DESC, dsr.ReviewID DESC
    """, params)

    counts = {r["Status"]: r["c"] for r in db.query(
        "SELECT Status, COUNT(*) c FROM DirectSaleReviews GROUP BY Status")}

    # For each row that's still Pending and has a salesperson, the Stock Issues it could be
    # assigned to on Confirm - same employee, same date (any status: even an already-
    # Reconciled issue can still take a late assignment, since its own StockIssueLines
    # figures were frozen at reconcile time regardless). Batched as one query per distinct
    # (employee, date) pair rather than one query per row.
    candidate_keys = {(r["EmployeeID"], r["SaleDate"]) for r in rows
                      if r["Status"] == "Pending" and r["EmployeeID"] and r["SaleDate"]}
    candidates_by_key = {}
    for employee_id, sale_date in candidate_keys:
        candidates_by_key[(employee_id, sale_date)] = db.query(
            """SELECT IssueID, IssueNumber, Status FROM StockIssues WHERE EmployeeID=? AND IssueDate=?
               ORDER BY IssueID""", (employee_id, sale_date))

    return render_template("direct_sale_review_list.html", rows=rows, status_filter=status_filter,
                            q=q, counts=counts, candidates_by_key=candidates_by_key)


@bp.route("/inventory/direct-sale-review/<int:review_id>/mark", methods=["POST"])
@admin_required
def direct_sale_review_mark(review_id):
    new_status = request.form.get("status")
    if new_status not in ("Confirmed", "Flagged", "Pending"):
        flash("Invalid review status.", "error")
        return redirect(url_for("direct_sale_review_list"))
    current = get_current_user()
    notes = (request.form.get("notes") or "").strip()

    # Flag/Reopen never include a stock_issue_id field at all - this review's existing
    # assignment (if any) is left exactly as it was. Only Confirm's own form (see
    # direct_sale_review_list.html) submits this field, always (set to '' for its
    # "don't assign" option), so that's what actually changes or clears it.
    existing = db.query("SELECT StockIssueID FROM DirectSaleReviews WHERE ReviewID=?", (review_id,), one=True)
    stock_issue_id = existing["StockIssueID"] if existing else None
    if "stock_issue_id" in request.form:
        stock_issue_id_raw = (request.form.get("stock_issue_id") or "").strip()
        stock_issue_id = None
        if stock_issue_id_raw:
            # Sanity check: only accept an issue that actually belongs to this review's own
            # salesperson - never let a stray/mistyped ID assign this quantity to someone
            # else's Stock Issue.
            review_sale = db.query("""SELECT s.EmployeeID FROM DirectSaleReviews dsr
                                    JOIN InventoryTransactions it ON it.TransactionID = dsr.TransactionID
                                    LEFT JOIN Sales s ON s.SaleID = it.RefID AND it.RefType='Sale'
                                    WHERE dsr.ReviewID=?""", (review_id,), one=True)
            issue = db.query("SELECT IssueID, EmployeeID FROM StockIssues WHERE IssueID=?",
                             (int(stock_issue_id_raw),), one=True) if stock_issue_id_raw.isdigit() else None
            if issue and review_sale and review_sale["EmployeeID"] == issue["EmployeeID"]:
                stock_issue_id = issue["IssueID"]
            else:
                flash("Could not assign to that Stock Issue (it doesn't belong to this sale's salesperson) — "
                      "left unassigned.", "warning")

    db.execute("""UPDATE DirectSaleReviews SET Status=?, ReviewedByUserID=?, ReviewedByUsername=?,
                ReviewedAt=datetime('now'), Notes=?, StockIssueID=? WHERE ReviewID=?""",
               (new_status, current["UserID"] if current else None,
                current["Username"] if current else None, notes or None, stock_issue_id, review_id))
    flash(f"Marked as {new_status}.", "success")
    return redirect(url_for("direct_sale_review_list", status=request.form.get("return_status", "Pending")))


def get_unassigned_customer_id():
    """The single system 'Unassigned' customer that reconciled Stock Issue
    sales are billed to until reassigned - created on first use."""
    row = db.query("SELECT CustomerID FROM Customers WHERE IsUnassignedBucket=1", one=True)
    if row:
        return row["CustomerID"]
    return db.execute("""INSERT INTO Customers (CustomerName, Active, IsUnassignedBucket)
                       VALUES ('Unassigned (Van/Route Sales)', 1, 1)""")


# ---------------------------------------------------------------------
# Stock Issues (van/route sales: issue stock to a salesperson, reconcile
# sold/returned/cash at day's end)
# ---------------------------------------------------------------------


@bp.route("/stock-issues")
def stock_issues_list():
    issues = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                       JOIN Employees e ON e.EmployeeID = si.EmployeeID
                       ORDER BY si.IssueDate DESC, si.IssueID DESC""")
    line_agg_rows = db.query("""SELECT IssueID,
                              COALESCE(SUM(QtySold), 0) AS qty_sold,
                              COALESCE(SUM(DiscountAmount), 0) AS discount,
                              COALESCE(SUM(SchemeClaimAmount), 0) AS scheme,
                              COALESCE(SUM(QtyIssued - QtySold - QtyReturned - QtyFree), 0) AS unaccounted
                              FROM StockIssueLines GROUP BY IssueID""")
    line_agg_by_issue = {r["IssueID"]: r for r in line_agg_rows}
    issues = [dict(i) for i in issues]
    target_cache = {}
    for i in issues:
        agg = line_agg_by_issue.get(i["IssueID"])
        i["QtySoldTotal"] = round(agg["qty_sold"], 2) if agg else 0
        i["DiscountTotal"] = round(agg["discount"], 2) if agg else 0
        i["SchemeTotal"] = round(agg["scheme"], 2) if agg else 0
        i["UnaccountedTotal"] = round(agg["unaccounted"], 2) if agg else 0
        # This employee's whole-month Qty Sold target progress, if one is set for the
        # issue's month - a quick "on pace or not" signal without leaving the list.
        y, m = int(i["IssueDate"][:4]), int(i["IssueDate"][5:7])
        cache_key = (i["EmployeeID"], y, m)
        if cache_key not in target_cache:
            target_cache[cache_key] = get_employee_month_target_progress(i["EmployeeID"], y, m)
        i["TargetProgress"] = target_cache[cache_key]
    totals = {
        "expected": sum(i["ExpectedAmount"] or 0 for i in issues),
        "collected": sum(i["CashCollected"] or 0 for i in issues),
        "discrepancy": sum(i["Discrepancy"] or 0 for i in issues),
        "qty_sold": sum(i["QtySoldTotal"] for i in issues),
        "discount": sum(i["DiscountTotal"] for i in issues),
        "scheme": sum(i["SchemeTotal"] for i in issues),
        "unaccounted": sum(i["UnaccountedTotal"] for i in issues),
    }
    return render_template("stock_issues_list.html", issues=issues, totals=totals,
                            columns=get_effective_columns("StockIssue"))


def product_qty_sold_for_issue(issue_id, employee_id, sale_date, product_id):
    """Ground-truth Qty Sold for ONE specific Stock Issue's line - safe even when a
    salesperson has more than one Stock Issue for the same employee/date (see
    find_open_stock_issue()'s docstring on how that arises: a second "Issue Stock" done
    instead of "Add More Products").

    When this is the ONLY Stock Issue for that employee/date (the overwhelmingly common
    case), this returns exactly what true_product_qty_sold() always has - zero behavior
    change there.

    When SEVERAL Stock Issues exist for the same employee/date, true_product_qty_sold()
    can no longer be trusted as this issue's own figure - it hands the SAME full-day total
    to every sibling issue's matching product line, double- (or triple-) crediting the same
    physical sale across more than one issue's reconciliation. In that case this instead
    counts only what's unambiguously THIS issue's:
      - Sales LIVE-CREDITED to one of this issue's own lines (SaleStockIssueLinks) - already
        issue-specific, no ambiguity.
      - Sales that fell through to a direct warehouse deduction AND were explicitly assigned
        to this issue when their Direct Sale Review was confirmed (DirectSaleReviews.
        StockIssueID) - see direct_sale_review_mark().
    A direct sale not yet confirmed/assigned doesn't count toward ANY sibling issue yet,
    rather than being guessed at and risking being credited to more than one - it shows up
    here once it's assigned."""
    siblings = db.query("SELECT IssueID FROM StockIssues WHERE EmployeeID=? AND IssueDate=?",
                        (employee_id, sale_date))
    if len(siblings) <= 1:
        return true_product_qty_sold(employee_id, sale_date, product_id)

    credited = db.query("""SELECT COALESCE(SUM(ssl.QtyApplied), 0) q FROM SaleStockIssueLinks ssl
                         JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                         WHERE sil.IssueID=? AND sil.ProductID=?""", (issue_id, product_id), one=True)["q"] or 0
    assigned_direct = db.query("""SELECT COALESCE(SUM(-it.QtyChange), 0) q
                                FROM DirectSaleReviews dsr
                                JOIN InventoryTransactions it ON it.TransactionID = dsr.TransactionID
                                JOIN Sales s ON s.SaleID = it.RefID AND it.RefType='Sale'
                                WHERE dsr.StockIssueID=? AND it.ProductID=? AND it.TransactionType='Sale'
                                  AND s.EmployeeID=? AND s.SaleDate=? AND s.Status<>'Cancelled'""",
                                (issue_id, product_id, employee_id, sale_date), one=True)["q"] or 0
    return round(credited + assigned_direct, 4)


def true_product_discount_sold(employee_id, sale_date, product_id):
    """The Discount-column equivalent of true_product_qty_sold(): the real total discount
    given across every one of a salesperson's Sales for one product/date, computed directly
    from SalesLines - independent of StockIssueLines.DiscountAmount, which only reflects
    whatever was credited to that line plus whatever an Admin typed at reconcile time. A
    Sale that fell through to a direct warehouse deduction (see true_product_qty_sold) still
    has its own real discount recorded on its own invoice, which this counts but the Stock
    Issue line's own DiscountAmount does not - by design, that's a different, separately-
    invoiced sale, not a mistake. Used to pre-fill Reconcile's Discount Rs field so it isn't
    left understating the true figure the way Qty Sold used to."""
    row = db.query("""SELECT COALESCE(SUM(sl.DiscountAmount), 0) q FROM SalesLines sl
                    JOIN Sales s ON s.SaleID = sl.SaleID
                    WHERE s.EmployeeID=? AND s.SaleDate=? AND sl.ProductID=? AND s.Status<>'Cancelled'""",
                   (employee_id, sale_date, product_id), one=True)
    return row["q"] or 0


@bp.route("/stock-issues/new", methods=["GET", "POST"])
def stock_issue_form():
    if request.method == "POST":
        f = request.form
        employee_id = int(f["employee_id"])
        issue_date = f["issue_date"]
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        if not lines:
            flash("Add at least one product line.", "error")
            return redirect(url_for("stock_issue_form"))

        issue_id = db.execute("""INSERT INTO StockIssues (IssueNumber, EmployeeID, IssueDate, Status, Notes)
                    VALUES (?,?,?,?,?)""", (next_stock_issue_number(), employee_id, issue_date, "Issued", f.get("notes", "")))
        log_stock_issue_change(issue_id, "Created")
        netted_msgs = []
        for prod_id, qty, price in lines:
            # Consolidates same-product lines within this one submission too (e.g. the same
            # product picked on two rows), not just across a later "Add More Products" top-up.
            netted = stock_issue_post_line(issue_id, prod_id, qty, price, issue_date,
                                            "Issued to salesperson", employee_id=employee_id)
            if netted > 0:
                prod = db.query("SELECT ProductName FROM Products WHERE ProductID=?", (prod_id,), one=True)
                netted_msgs.append(f"{netted:g} {prod['ProductName'] if prod else ''}".strip())
        flash("Stock issued.", "success")
        if netted_msgs:
            flash("Already sold directly today before this issue existed, so the warehouse "
                  "deduction was reduced to avoid double-counting: " + ", ".join(netted_msgs) + ".", "warning")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    products = get_products_with_stock()
    return render_template("stock_issue_form.html", employees=employees, products=products, today=today_str())


@bp.route("/stock-issues/<int:issue_id>/add-lines", methods=["GET", "POST"])
def stock_issue_add_lines(issue_id):
    """Add more product lines to a Stock Issue that hasn't been reconciled yet -
    covers a salesperson getting re-stocked more than once in the same day
    (morning batch, then a top-up in the afternoon) without needing a second,
    separate Stock Issue for the same day/person."""
    issue = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    if issue["Status"] == "Reconciled":
        flash("This issue has already been reconciled — more products can't be added to it. "
              "Issue a new Stock Issue instead, or use 'Edit Reconciliation' if you need to correct it.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        lines = [(int(p), float(q), float(pr)) for p, q, pr in zip(product_ids, qtys, prices) if p and q]
        if not lines:
            flash("Add at least one product line.", "error")
            return redirect(url_for("stock_issue_add_lines", issue_id=issue_id))

        add_date = f.get("issue_date") or issue["IssueDate"]
        netted_msgs = []
        for prod_id, qty, price in lines:
            # Consolidated: a product already on this issue (from the original Issue Stock
            # action or an earlier top-up today) gets its existing line topped up in place
            # (qty-weighted average price) rather than appearing as a separate line — so
            # Reconcile shows one row per product for the day, not one row per top-up.
            netted = stock_issue_post_line(issue_id, prod_id, qty, price, add_date,
                                            "Additional stock issued to salesperson (same-day top-up)",
                                            employee_id=issue["EmployeeID"])
            if netted > 0:
                prod = db.query("SELECT ProductName FROM Products WHERE ProductID=?", (prod_id,), one=True)
                netted_msgs.append(f"{netted:g} {prod['ProductName'] if prod else ''}".strip())
        flash(f"Added {len(lines)} more product line(s) to this stock issue "
              f"(consolidated into existing product lines where applicable).", "success")
        if netted_msgs:
            flash("Already sold directly today before being added to this issue, so the warehouse "
                  "deduction was reduced to avoid double-counting: " + ", ".join(netted_msgs) + ".", "warning")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))

    products = get_products_with_stock()
    existing_product_ids = {r["ProductID"] for r in db.query(
        "SELECT ProductID FROM StockIssueLines WHERE IssueID=?", (issue_id,))}
    direct_sales_warning = direct_sales_today_for_employee(
        issue["EmployeeID"], issue["IssueDate"], exclude_product_ids=existing_product_ids)
    return render_template("stock_issue_add_lines.html", issue=issue, products=products, today=today_str(),
                            direct_sales_warning=direct_sales_warning)


@bp.route("/stock-issues/<int:issue_id>")
def stock_issue_view(issue_id):
    issue = db.query("""SELECT si.*, e.EmployeeName, e.Phone FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    lines = db.query("""SELECT sil.*, pr.ProductName, pr.Unit FROM StockIssueLines sil
                      JOIN Products pr ON pr.ProductID = sil.ProductID WHERE sil.IssueID=?""", (issue_id,))
    # Column totals for the line table below - every numeric column except Unit Price
    # (summing a per-unit price across different products isn't a meaningful figure).
    line_totals = {
        "qty_issued": sum(l["QtyIssued"] or 0 for l in lines),
        "qty_sold": sum(l["QtySold"] or 0 for l in lines),
        "qty_returned": sum(l["QtyReturned"] or 0 for l in lines),
        "qty_free": sum(l["QtyFree"] or 0 for l in lines),
        "discount": sum(l["DiscountAmount"] or 0 for l in lines),
        "scheme_claim": sum(l["SchemeClaimAmount"] or 0 for l in lines),
        "unaccounted": sum((l["QtyIssued"] or 0) - (l["QtySold"] or 0) - (l["QtyReturned"] or 0) - (l["QtyFree"] or 0)
                            for l in lines),
    }
    y, m = int(issue["IssueDate"][:4]), int(issue["IssueDate"][5:7])
    target_progress = get_employee_month_target_progress(issue["EmployeeID"], y, m)
    # Full-view reconciliation data: every Sale credited against this issue (from the Sales
    # tab), with its Cash/Bank/Discount breakdown, so an Admin/Manager can tally cash+bank
    # collected against expected stock value in one place without hopping between screens.
    credited_sales_full = db.query("""
        SELECT s.SaleID, s.InvoiceNumber, s.CashAmount, s.BankAmount, s.AmountReceived,
               COALESCE(SUM(sl.DiscountAmount), 0) AS Discount, COALESCE(SUM(sl.TaxableValue
                 + sl.CGSTAmount + sl.SGSTAmount + sl.IGSTAmount), 0) AS LineTotal
        FROM (SELECT DISTINCT SaleID FROM SaleStockIssueLinks ssl
              JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID WHERE sil.IssueID=?) x
        JOIN Sales s ON s.SaleID = x.SaleID
        LEFT JOIN SalesLines sl ON sl.SaleID = s.SaleID
        GROUP BY s.SaleID
    """, (issue_id,))
    sales_cash_total = round(sum(cs["CashAmount"] or 0 for cs in credited_sales_full), 2)
    sales_bank_total = round(sum(cs["BankAmount"] or 0 for cs in credited_sales_full), 2)
    sales_discount_total = round(sum(cs["Discount"] or 0 for cs in credited_sales_full), 2)
    audit_log = db.query("SELECT * FROM StockIssueAuditLog WHERE IssueID=? ORDER BY LogID", (issue_id,))
    return render_template("stock_issue_view.html", issue=issue, lines=lines, today=today_str(),
                            money_locked=stock_issue_money_locked(issue), line_totals=line_totals,
                            target_progress=target_progress, credited_sales_full=credited_sales_full,
                            sales_cash_total=sales_cash_total, sales_bank_total=sales_bank_total,
                            sales_discount_total=sales_discount_total, audit_log=audit_log)


@bp.route("/stock-issues/<int:issue_id>/approve", methods=["POST"])
def stock_issue_approve(issue_id):
    """Manager/Admin-only: approves a Stock Issue that was auto-created behind
    a salesperson's Sales-tab entry (ReviewStatus='Pending'), so it can then
    be reconciled. Does not change anything about the issue's lines/stock —
    it's purely a human sign-off that the auto-created figures look right."""
    user = get_current_user()
    if not user or user["Role"] not in ("Manager", "Admin"):
        flash("Only Manager/Admin accounts can approve a pending Stock Issue.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    db.execute("UPDATE StockIssues SET ReviewStatus='Reviewed' WHERE IssueID=?", (issue_id,))
    flash("Stock Issue approved — it can now be reconciled.", "success")
    return redirect(url_for("stock_issue_view", issue_id=issue_id))


def stock_issue_money_locked(issue):
    """True if a Stock Issue's reconciliation can no longer be safely edited or
    deleted - a due payment or scheme claim has already been recorded against
    it, so changing the underlying figures would orphan that money trail."""
    due_payments = db.query("SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?",
                            (issue["IssueID"],), one=True)["c"]
    return due_payments > 0 or issue["ClaimStatus"] != "Not Claimed"


@bp.route("/stock-issues/<int:issue_id>/reconcile", methods=["GET", "POST"])
def stock_issue_reconcile(issue_id):
    # Whole request in one IMMEDIATE transaction: the Status/is_reedit read below can't be interleaved
    # with another request's reconcile or delete.
    if request.method == "POST":
        with db.transaction():
            return _stock_issue_reconcile(issue_id)
    return _stock_issue_reconcile(issue_id)


def _stock_issue_reconcile(issue_id):
    # Joins Employees for EmployeeName - the template's page title and (new) Target-progress
    # box both display it; a plain "SELECT * FROM StockIssues" here previously left it blank.
    issue = db.query("""SELECT si.*, e.EmployeeName FROM StockIssues si
                      JOIN Employees e ON e.EmployeeID = si.EmployeeID WHERE si.IssueID=?""", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    if issue["ReviewStatus"] == "Pending":
        flash("This Stock Issue was auto-created from a Sales-tab entry and is awaiting Manager/Admin review — "
              "approve it first before reconciling.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    is_reedit = issue["Status"] == "Reconciled"
    money_locked = False
    # Reconciling a Stock Issue (fresh or re-editing an already-reconciled one) is now
    # Admin-only - Staff/Supervisor/Manager can still view/approve/create Stock Issues,
    # but the reconcile step itself is restricted per explicit request.
    current = get_current_user()
    if not current or current["Role"] != "Admin":
        flash("Only Admin accounts can reconcile a Stock Issue.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    if is_reedit:
        # A due payment or scheme claim already recorded no longer blocks re-editing outright -
        # the Admin instead chooses, right on this form, whether to keep that history (just
        # recompute the figures on top) or clear it and start fresh (same reversal-then-redo
        # approach Delete already uses), since the old due-payment/claim amounts were based on
        # figures that are about to change.
        money_locked = stock_issue_money_locked(issue)

    lines = db.query("""SELECT sil.*, pr.ProductName, pr.Unit, pr.CostPrice, pr.SchemeName, pr.SchemePercent
                      FROM StockIssueLines sil
                      JOIN Products pr ON pr.ProductID = sil.ProductID WHERE sil.IssueID=?""", (issue_id,))

    if request.method == "POST":
        f = request.form
        # Guardrail: correcting an already-reconciled issue must be a deliberate, reasoned
        # action, not a silent re-save - a "Reconciled" figure is supposed to be a closed
        # book. Blocking here (before anything is touched) if no reason was given is what
        # makes "Reopen & Correct" an explicit, logged step rather than indistinguishable
        # from a fresh reconciliation (see the audit log entries posted below).
        reopen_reason = (f.get("reopen_reason") or "").strip()
        if is_reedit and not reopen_reason:
            flash("A reason is required to correct an already-reconciled Stock Issue - enter what's being "
                  "fixed and why in 'Reason for this correction', then save again. Nothing was changed.", "error")
            return redirect(url_for("stock_issue_reconcile", issue_id=issue_id))
        # How much of each line's Qty Sold was already billed on a real customer's own
        # Sales-tab invoice - the auto-created "Unassigned" Sale below must only cover
        # whatever's LEFT beyond that, or it re-invoices the same units a second time
        # (double-counting Sales Today/Month, GST, and P&L revenue). This used to only
        # count quantity CREDITED to this line (via SaleStockIssueLinks), which was correct
        # as long as Qty Sold above was pre-filled from that same credited figure - but
        # since the Reconcile GET handler now pre-fills Qty Sold with the true ground-truth
        # total (true_product_qty_sold(), which also includes units that fell through to a
        # DIRECT warehouse deduction rather than being credited - see that function's
        # docstring), "already invoiced" must cover those too, or they'd get invoiced a
        # second time here. true_product_qty_sold() is exactly "how much of this product
        # was already invoiced anywhere today for this salesperson", so it's used directly.
        # The whole reconcile/re-reconcile save is one atomic transaction (see
        # db.transaction()) - it makes well over a dozen separate inserts/updates
        # (StockIssueLines, StockIssues, inventory postings, the auto-invoiced
        # Sale, audit log entries); a failure partway through used to leave a
        # permanently half-corrected reconciliation. Now either all of it lands,
        # or none of it does.
        with db.transaction():
            already_invoiced_by_line = {
                l["LineID"]: product_qty_sold_for_issue(issue_id, issue["EmployeeID"], issue["IssueDate"], l["ProductID"])
                for l in lines
            }
            cash_amount = float(f.get("cash_amount") or 0)
            bank_amount = float(f.get("bank_amount") or 0)
            cash_collected = round(cash_amount + bank_amount, 2)
            expected_amount = 0.0
            scheme_amount = 0.0
            if is_reedit:
                # Remove the Return-In entries the previous reconciliation posted (but NOT the
                # original Issue transaction) before re-posting a fresh one below. Also clean up
                # any old-style "Free Scheme" row(s) this issue may still have from before the
                # double-deduction fix (see migrate_fix_free_scheme_double_deduction.py) - and,
                # if that migration already corrected them, the compensating "FreeSchemeFix"
                # row(s) too, so re-editing doesn't leave an orphaned correction with nothing
                # left for it to correct (which would overstate stock instead of just being
                # neutral, now that fresh reconciles no longer post a Free Scheme row at all).
                old_free_scheme_ids = [r["TransactionID"] for r in db.query(
                    """SELECT TransactionID FROM InventoryTransactions
                       WHERE RefType='StockIssue' AND RefID=? AND TransactionType='Free Scheme'""", (issue_id,))]
                db.execute("""DELETE FROM InventoryTransactions WHERE RefType='StockIssue' AND RefID=?
                            AND TransactionType IN ('Return-In','Free Scheme')""", (issue_id,))
                for old_id in old_free_scheme_ids:
                    db.execute("DELETE FROM InventoryTransactions WHERE RefType='FreeSchemeFix' AND RefID=?", (old_id,))
                if money_locked and f.get("money_data_action") == "clear":
                    # Admin chose to clear existing due-payment/claim history rather than keep it,
                    # since it was based on figures this edit is about to change - reset it the same
                    # way Delete's reversal does, so nothing stale is left referencing old amounts.
                    db.execute("DELETE FROM StockIssueDuePayments WHERE IssueID=?", (issue_id,))
                    db.execute("""UPDATE StockIssues SET ClaimStatus='Not Claimed', ClaimedAt=NULL,
                                ClaimedAmount=NULL, ReceivedAt=NULL, ReceivedAmount=NULL, ClaimNotes=NULL
                                WHERE IssueID=?""", (issue_id,))
            sale_lines = []  # (product_id, qty_sold, effective_rate) - feeds the auto-created "Unassigned" Sale below
            line_audit_entries = []  # collected here, written to StockIssueAuditLog once the save succeeds below
            for line in lines:
                qty_sold = float(f.get(f"qty_sold_{line['LineID']}") or 0)
                qty_returned = float(f.get(f"qty_returned_{line['LineID']}") or 0)
                qty_free = float(f.get(f"qty_free_{line['LineID']}") or 0)
                discount_amount = float(f.get(f"discount_amount_{line['LineID']}") or 0)
                scheme_claim_amount = float(f.get(f"scheme_claim_{line['LineID']}") or 0)
                line_comments = (f.get(f"comments_{line['LineID']}") or "").strip()
                if is_reedit and abs(qty_sold - (line["QtySold"] or 0)) > 0.001:
                    line_audit_entries.append((f"{line['ProductName']} — Qty Sold", line["QtySold"] or 0, qty_sold))
                if is_reedit and abs(qty_returned - (line["QtyReturned"] or 0)) > 0.001:
                    line_audit_entries.append((f"{line['ProductName']} — Qty Returned", line["QtyReturned"] or 0, qty_returned))
                if is_reedit and abs(discount_amount - (line["DiscountAmount"] or 0)) > 0.001:
                    line_audit_entries.append((f"{line['ProductName']} — Discount ₹", line["DiscountAmount"] or 0, discount_amount))
                db.execute("""UPDATE StockIssueLines SET QtySold=?, QtyReturned=?, QtyFree=?,
                            DiscountAmount=?, SchemeClaimAmount=?, LineComments=? WHERE LineID=?""",
                           (qty_sold, qty_returned, qty_free, discount_amount, scheme_claim_amount,
                            line_comments, line["LineID"]))
                expected_amount += (qty_sold * line["UnitPrice"]) - discount_amount
                # Scheme Amount (claimable back from the company) is now driven purely by the
                # directly-entered Scheme Claim Rs field, NOT by Discount Rs - a discount is a
                # real margin reduction the distributor absorbs, never something the company
                # reimburses, so it must never be counted as claimable.
                scheme_amount += scheme_claim_amount
                if qty_sold > 0:
                    effective_rate = round(max((qty_sold * line["UnitPrice"]) - discount_amount, 0) / qty_sold, 4)
                    already_invoiced = already_invoiced_by_line.get(line["LineID"], 0) or 0
                    auto_invoice_qty = round(max(qty_sold - already_invoiced, 0), 4)
                    if auto_invoice_qty > 0:
                        sale_lines.append((line["ProductID"], auto_invoice_qty, effective_rate))
                if qty_returned > 0:
                    db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                                QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                               (line["ProductID"], today_str(), "Return-In", qty_returned, "StockIssue", issue_id,
                                "Returned unsold from stock issue"))
                # NOTE: qty_free posts NO InventoryTransactions row, same as qty_sold above - the
                # original "Issue" transaction already deducted the FULL QtyIssued from the ledger
                # at issue time, and units given away free (like units sold) never come back, so
                # that original deduction already fully accounts for them. A previous version of
                # this code also posted a "Free Scheme" -qty_free row here on top of that, which
                # double-deducted every free-given unit from stock forever (see
                # migrate_fix_free_scheme_double_deduction.py, which reverses the historical
                # damage this caused - it's very likely most of what shows up as "missing"
                # inventory on the Reconciliation report). Only qty_returned needs a transaction
                # here, because that's the one outcome where stock genuinely comes back.
            expected_amount = round(max(expected_amount, 0), 2)
            scheme_amount = round(scheme_amount, 2)
            discrepancy = round(cash_collected - expected_amount, 2)
            amount_due = round(max(expected_amount - cash_collected, 0), 2)
            if amount_due <= 0:
                payment_status = "Paid"
            elif cash_collected <= 0:
                payment_status = "Unpaid"
            else:
                payment_status = "Partial"
            db.execute("""UPDATE StockIssues SET Status='Reconciled', CashCollected=?, CashAmount=?, BankAmount=?,
                        ExpectedAmount=?, Discrepancy=?, AmountDue=?, PaymentStatus=?, SchemeAmount=?, ReconciledAt=?, Notes=?
                        WHERE IssueID=?""",
                       (cash_collected, cash_amount, bank_amount, expected_amount, discrepancy, amount_due,
                        payment_status, scheme_amount,
                        datetime.now().isoformat(timespec="seconds"), f.get("notes", issue["Notes"]), issue_id))

            # Audit trail: every reconcile/re-reconcile is logged (shown on the issue's own
            # Activity panel, collapsed by default). A re-edit additionally logs the mandatory
            # reason and every figure that actually changed, so "Reconciled" numbers moving is
            # never silent again - this is the guardrail from the front-end blocking above,
            # completed on the back end.
            if is_reedit:
                log_stock_issue_change(issue_id, "Reopened & Re-Reconciled", "Reason", None, reopen_reason)
                for field_name, old_val, new_val in line_audit_entries:
                    log_stock_issue_change(issue_id, "Line Corrected", field_name, f"{old_val:g}", f"{new_val:g}")
                log_stock_issue_change(
                    issue_id, "Re-Reconciled", "Expected / Collected / Discrepancy",
                    f"₹{issue['ExpectedAmount'] or 0:.2f} / ₹{issue['CashCollected'] or 0:.2f} / ₹{issue['Discrepancy'] or 0:.2f}",
                    f"₹{expected_amount:.2f} / ₹{cash_collected:.2f} / ₹{discrepancy:.2f}")
            else:
                log_stock_issue_change(issue_id, "Reconciled", "Expected / Collected / Discrepancy", None,
                                        f"₹{expected_amount:.2f} / ₹{cash_collected:.2f} / ₹{discrepancy:.2f}")

            # Auto-create (or, on re-edit, update in place) a real GST Sale for the sold units,
            # billed to the system "Unassigned" customer until someone reassigns it via
            # /sales/<id>/reassign. Stock is NOT deducted again here (post_inventory=False) -
            # the Issue/Return-In/Free-Scheme transactions above already account for it.
            company = get_company_settings()
            existing_sale_id = issue["SaleID"]
            if sale_lines:
                existing_invoice = None
                if existing_sale_id:
                    existing_row = db.query("SELECT InvoiceNumber FROM Sales WHERE SaleID=?", (existing_sale_id,), one=True)
                    existing_invoice = existing_row["InvoiceNumber"] if existing_row else None
                new_sale_id = create_sale(
                    customer_id=get_unassigned_customer_id(), sale_date=issue["IssueDate"], status="Completed",
                    payment_status=payment_status, payment_due_date=None, amount_received=cash_collected,
                    notes=f"Auto-created from Stock Issue #{issue_id} reconciliation",
                    place_of_supply_code=company["StateCode"], lines=sale_lines,
                    invoice_no=existing_invoice, sale_id=existing_sale_id, post_inventory=False)
                if new_sale_id != existing_sale_id:
                    db.execute("UPDATE StockIssues SET SaleID=? WHERE IssueID=?", (new_sale_id, issue_id))
            elif existing_sale_id:
                # Re-edited down to nothing sold - nothing left to bill, drop the link (the Sale
                # itself is left as-is rather than deleted, since it may already have been
                # reassigned/split to real customers by this point).
                db.execute("UPDATE StockIssues SET SaleID=NULL WHERE IssueID=?", (issue_id,))

            msg = f"Reconciled — expected ₹{expected_amount:.2f}, collected ₹{cash_collected:.2f}"
            if scheme_amount:
                msg += f", ₹{scheme_amount:.2f} given as discount/free scheme (claimable from company)"
            if amount_due > 0:
                msg += f". ₹{amount_due:.2f} still due — record it later with 'Record Due Payment'."
                flash(msg, "warning")
            else:
                msg += "."
                flash(msg, "success")
            return redirect(url_for("stock_issue_view", issue_id=issue_id))

    # Pre-fill each line's Qty Sold with the ground-truth total (SalesLines-based) whenever
    # it's higher than what's stored on StockIssueLines.QtySold - see true_product_qty_sold()
    # for why the stored figure can under-report on a fresh (not yet reconciled) issue whose
    # Qty Issued capacity was topped up in installments. Only done for a fresh reconciliation,
    # never a re-edit, so an Admin's own already-reconciled figures are never silently changed.
    # Same idea, same reasoning, for Discount Rs: a Sale that fell through to a direct
    # deduction still has its own real discount on its own invoice, which StockIssueLines.
    # DiscountAmount doesn't include (it's not this line's own bookkeeping) but Live Sales
    # Monitor's Discount column correctly adds back in - pre-filling here so both agree by
    # default instead of only after someone notices the gap and manually retypes it.
    lines = [dict(l) for l in lines]
    for l in lines:
        l["TrueQtySold"] = product_qty_sold_for_issue(issue_id, issue["EmployeeID"], issue["IssueDate"], l["ProductID"])
        l["StoredQtySold"] = l["QtySold"] or 0
        if not is_reedit and l["TrueQtySold"] > l["StoredQtySold"] + 0.001:
            l["QtySold"] = l["TrueQtySold"]
        l["TrueDiscount"] = true_product_discount_sold(issue["EmployeeID"], issue["IssueDate"], l["ProductID"])
        l["StoredDiscount"] = l["DiscountAmount"] or 0
        if not is_reedit and l["TrueDiscount"] > l["StoredDiscount"] + 0.001:
            l["DiscountAmount"] = l["TrueDiscount"]

    y, m = int(issue["IssueDate"][:4]), int(issue["IssueDate"][5:7])
    target_progress = get_employee_month_target_progress(issue["EmployeeID"], y, m)
    # Sales entered directly (Sales tab, with this issue's salesperson selected) that have
    # already credited this issue's Qty Sold - shown so whoever reconciles isn't puzzled by
    # pre-filled Qty Sold figures they didn't type in themselves.
    credited_sales = db.query("""SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, pr.ProductName,
                               ssl.QtyApplied FROM SaleStockIssueLinks ssl
                               JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                               JOIN Sales s ON s.SaleID = ssl.SaleID
                               JOIN Products pr ON pr.ProductID = sil.ProductID
                               WHERE sil.IssueID=? ORDER BY s.SaleID""", (issue_id,))
    # Cash/Bank already collected via those same credited Sales - pre-filled into the Cash/Bank
    # fields below (on a FRESH reconciliation only) so whoever reconciles sees the running total
    # instead of re-typing the whole day's collection from scratch and unknowingly double-
    # counting money that's already recorded per-Sale (this is what the Live Sales Monitor's
    # Collected figure used to double up on before this pre-fill existed).
    credited_sales_money = db.query("""SELECT COALESCE(SUM(DISTINCT_SALE.CashAmount),0) cash,
                                     COALESCE(SUM(DISTINCT_SALE.BankAmount),0) bank FROM (
                                       SELECT DISTINCT s.SaleID, s.CashAmount, s.BankAmount
                                       FROM SaleStockIssueLinks ssl
                                       JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                                       JOIN Sales s ON s.SaleID = ssl.SaleID
                                       WHERE sil.IssueID=?
                                     ) DISTINCT_SALE""", (issue_id,), one=True)
    return render_template("stock_issue_reconcile.html", issue=issue, lines=lines, is_reedit=is_reedit,
                            money_locked=money_locked, target_progress=target_progress,
                            credited_sales=credited_sales, credited_sales_money=credited_sales_money)


@bp.route("/stock-issues/<int:issue_id>/delete", methods=["POST"])
@admin_required
def stock_issue_delete(issue_id):
    # Whole request in one IMMEDIATE transaction: the Status/is_reedit read below can't be interleaved
    # with another request's reconcile or delete.
    with db.transaction():
        return _stock_issue_delete(issue_id)


def _stock_issue_delete(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    # Sales that credited this issue's lines (SaleStockIssueLinks) are still sold: deleting the issue
    # would hit a foreign key and, worse, restore its whole issued stock while those units stay sold.
    credited = db.query("""SELECT COUNT(DISTINCT ssl.SaleID) c FROM SaleStockIssueLinks ssl
                         JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                         WHERE sil.IssueID=?""", (issue_id,), one=True)["c"]
    if credited:
        flash(f"Can't delete this Stock Issue: {credited} sale(s) are credited against it. Delete or "
              f"reassign those sales first, or use Reconcile to correct the figures instead. Nothing was changed.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    # A due payment or scheme claim already recorded against this issue used to block
    # deletion outright. It no longer does (Admin-only, as this whole route already is) -
    # instead the delete performs a full reversal: due-payment history and claim status
    # are erased along with the issue itself, exactly like every other figure on it.
    was_money_locked = stock_issue_money_locked(issue)
    due_payment_count = db.query("SELECT COUNT(*) c FROM StockIssueDuePayments WHERE IssueID=?",
                                 (issue_id,), one=True)["c"]
    # Deleting every InventoryTransaction this issue posted (Issue-out, any Return-In,
    # any Free Scheme) fully reverses its stock impact, since nothing else references them.
    db.execute("DELETE FROM InventoryTransactions WHERE RefType='StockIssue' AND RefID=?", (issue_id,))
    db.execute("DELETE FROM StockIssueDuePayments WHERE IssueID=?", (issue_id,))
    db.execute("DELETE FROM StockIssueLines WHERE IssueID=?", (issue_id,))
    db.execute("DELETE FROM StockIssueAuditLog WHERE IssueID=?", (issue_id,))
    sale_note = ""
    if issue["SaleID"]:
        # The linked "Unassigned" Sale (if it hasn't already been reassigned away) is deleted
        # too - its own InventoryTransactions are already none (post_inventory=False when it
        # was created), so this only removes the Sale/SalesLines rows, not stock. Null out the
        # StockIssues.SaleID reference first - it has no ON DELETE CASCADE, so deleting the
        # Sale while this issue still points at it would fail the foreign key check.
        db.execute("UPDATE StockIssues SET SaleID=NULL WHERE IssueID=?", (issue_id,))
        db.execute("DELETE FROM SalesLines WHERE SaleID=?", (issue["SaleID"],))
        db.execute("DELETE FROM Sales WHERE SaleID=?", (issue["SaleID"],))
        sale_note = " and its linked Sale"
    db.execute("DELETE FROM StockIssues WHERE IssueID=?", (issue_id,))
    msg = f"Stock issue deleted{sale_note}, and its stock impact reversed."
    if was_money_locked:
        reversed_bits = []
        if due_payment_count:
            reversed_bits.append(f"{due_payment_count} due payment record(s)")
        if issue["ClaimStatus"] != "Not Claimed":
            reversed_bits.append(f"its scheme claim (was {issue['ClaimStatus']})")
        msg += f" Also reversed: {', '.join(reversed_bits)}."
    flash(msg, "success")
    return redirect(url_for("stock_issues_list"))


@bp.route("/stock-issues/<int:issue_id>/collect-due", methods=["POST"])
def stock_issue_collect_due(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    f = request.form
    amount = round(float(f.get("amount") or 0), 2)
    if amount <= 0:
        flash("Enter an amount greater than zero.", "error")
        return redirect(url_for("stock_issue_view", issue_id=issue_id))
    payment_date = f.get("payment_date") or today_str()
    notes = (f.get("notes") or "").strip()
    db.execute("""INSERT INTO StockIssueDuePayments (IssueID, PaymentDate, Amount, Notes)
                VALUES (?,?,?,?)""", (issue_id, payment_date, amount, notes))
    new_cash_collected = round((issue["CashCollected"] or 0) + amount, 2)
    new_due = round(max((issue["AmountDue"] or 0) - amount, 0), 2)
    payment_status = "Paid" if new_due <= 0 else "Partial"
    new_discrepancy = round(new_cash_collected - (issue["ExpectedAmount"] or 0), 2)
    db.execute("UPDATE StockIssues SET CashCollected=?, AmountDue=?, PaymentStatus=?, Discrepancy=? WHERE IssueID=?",
               (new_cash_collected, new_due, payment_status, new_discrepancy, issue_id))
    if issue["SaleID"]:
        # Keep the linked "Unassigned" Sale's own payment figures in step with the issue's.
        db.execute("UPDATE Sales SET AmountReceived=?, PaymentStatus=? WHERE SaleID=?",
                   (new_cash_collected, payment_status, issue["SaleID"]))
    flash(f"Recorded ₹{amount:.2f} due payment. {'Fully settled.' if new_due <= 0 else f'₹{new_due:.2f} still due.'}",
          "success")
    return redirect(url_for("stock_issue_view", issue_id=issue_id))


@bp.route("/stock-issues/<int:issue_id>/claim", methods=["POST"])
def stock_issue_claim(issue_id):
    issue = db.query("SELECT * FROM StockIssues WHERE IssueID=?", (issue_id,), one=True)
    if not issue:
        flash("Stock issue not found.", "error")
        return redirect(url_for("stock_issues_list"))
    f = request.form
    action = f.get("action")
    notes = (f.get("notes") or "").strip()
    if action == "claimed":
        amount = round(float(f.get("claimed_amount") or issue["SchemeAmount"] or 0), 2)
        db.execute("""UPDATE StockIssues SET ClaimStatus='Claimed', ClaimedAt=?, ClaimedAmount=?,
                    ClaimNotes=? WHERE IssueID=?""",
                   (datetime.now().isoformat(timespec="seconds"), amount, notes, issue_id))
        flash(f"Marked ₹{amount:.2f} scheme claim as submitted to the company.", "success")
    elif action == "received":
        amount = round(float(f.get("received_amount") or issue["ClaimedAmount"] or issue["SchemeAmount"] or 0), 2)
        db.execute("""UPDATE StockIssues SET ClaimStatus='Received', ReceivedAt=?, ReceivedAmount=?,
                    ClaimNotes=? WHERE IssueID=?""",
                   (datetime.now().isoformat(timespec="seconds"), amount, notes, issue_id))
        flash(f"Marked ₹{amount:.2f} scheme claim as received from the company.", "success")
    else:
        flash("Unknown claim action.", "error")
    return redirect(url_for("stock_issue_view", issue_id=issue_id))


# ---------------------------------------------------------------------
# Scheme Claims - distributor-level claims not tied to any one product or
# Stock Issue (e.g. an annual volume rebate, a company-wide promo claim
# covering multiple products/months). Separate from, and doesn't touch,
# the per-Stock-Issue discount/free-scheme claim tracking above.
# ---------------------------------------------------------------------


@bp.route("/scheme-claims")
def scheme_claims_list():
    status_filter = request.args.get("status", "")
    sql = "SELECT * FROM SchemeClaims"
    args = ()
    if status_filter:
        sql += " WHERE Status=?"
        args = (status_filter,)
    sql += " ORDER BY ClaimDate DESC, ClaimID DESC"
    claims = db.query(sql, args)
    totals = {
        "pending": sum(c["ClaimAmount"] or 0 for c in claims if c["Status"] in ("Pending", "Claimed")),
        "received": sum((c["ReceivedAmount"] if c["ReceivedAmount"] is not None else c["ClaimAmount"]) or 0
                         for c in claims if c["Status"] in ("Received", "Completed")),
    }
    return render_template("scheme_claims_list.html", claims=claims, status_filter=status_filter, totals=totals)


@bp.route("/scheme-claims/new", methods=["GET", "POST"])
@bp.route("/scheme-claims/<int:claim_id>/edit", methods=["GET", "POST"])
def scheme_claim_form(claim_id=None):
    claim = db.query("SELECT * FROM SchemeClaims WHERE ClaimID=?", (claim_id,), one=True) if claim_id else None
    if request.method == "POST":
        f = request.form
        args = (f["scheme_name"], f["claim_date"], f.get("applicable_products", ""),
                f.get("description", ""), float(f.get("claim_amount") or 0), f.get("notes", ""))
        if claim_id:
            db.execute("""UPDATE SchemeClaims SET SchemeName=?, ClaimDate=?, ApplicableProducts=?,
                        Description=?, ClaimAmount=?, Notes=? WHERE ClaimID=?""", args + (claim_id,))
            flash("Scheme claim updated.", "success")
        else:
            new_id = db.execute("""INSERT INTO SchemeClaims (SchemeName, ClaimDate, ApplicableProducts,
                        Description, ClaimAmount, Notes, Status) VALUES (?,?,?,?,?,?,'Pending')""", args)
            flash("Scheme claim added.", "success")
        return redirect(url_for("scheme_claims_list"))
    return render_template("scheme_claim_form.html", claim=claim, today=today_str())


@bp.route("/scheme-claims/<int:claim_id>/mark", methods=["POST"])
def scheme_claim_mark(claim_id):
    claim = db.query("SELECT * FROM SchemeClaims WHERE ClaimID=?", (claim_id,), one=True)
    if not claim:
        flash("Scheme claim not found.", "error")
        return redirect(url_for("scheme_claims_list"))
    new_status = request.form.get("status")
    if new_status not in ("Claimed", "Received", "Completed"):
        flash("Unknown status.", "error")
        return redirect(url_for("scheme_claims_list"))
    now = datetime.now().isoformat(timespec="seconds")
    if new_status == "Claimed":
        db.execute("UPDATE SchemeClaims SET Status='Claimed', ClaimedAt=? WHERE ClaimID=?", (now, claim_id))
    elif new_status == "Received":
        received_amount = float(request.form.get("received_amount") or claim["ClaimAmount"])
        db.execute("""UPDATE SchemeClaims SET Status='Received', ReceivedAt=?, ReceivedAmount=?
                    WHERE ClaimID=?""", (now, received_amount, claim_id))
    else:  # Completed
        db.execute("UPDATE SchemeClaims SET Status='Completed' WHERE ClaimID=?", (claim_id,))
    flash(f"Scheme claim marked {new_status}.", "success")
    return redirect(url_for("scheme_claims_list"))


@bp.route("/scheme-claims/<int:claim_id>/delete", methods=["POST"])
@admin_required
def scheme_claim_delete(claim_id):
    db.execute("DELETE FROM SchemeClaims WHERE ClaimID=?", (claim_id,))
    flash("Scheme claim deleted.", "success")
    return redirect(url_for("scheme_claims_list"))
