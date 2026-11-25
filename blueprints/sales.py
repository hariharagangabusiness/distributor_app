"""sales routes."""
import bulk_import
import db
import os
from core.auth import admin_required, get_current_user
from core.helpers import locked_issue_for_sale, locked_sale_message, INDIAN_STATES, MONTH_NAMES, STATE_NAME_BY_CODE, SaleStockIssueLockedError, UPLOAD_ROOT, _now_ist, amount_in_words, build_target_view, create_sale, custom_field_attachment_module, get_attachments, get_company_settings, get_custom_attachments, get_custom_field_defs, get_custom_values, get_customer_due_info, get_effective_columns, get_effective_form_fields, get_products_with_stock, get_target_buckets, log_stock_issue_change, next_customer_account_number, pct, reverse_sale_stock_issue_links, sale_balance_due, sale_due_amount, save_custom_fields, target_days_elapsed, target_month_bounds, today_str, unlinked_sale_rows
from datetime import date, datetime, timedelta
from flask import flash, redirect, render_template, request, url_for



from flask import Blueprint

bp = Blueprint("sales", __name__)


def parse_form_number(value, field_label):
    """Turns a form field's text into a float, tolerating the way people
    naturally type numbers (Indian-style thousands separators like
    '1,500', a stray ₹ symbol, surrounding spaces) instead of crashing the
    whole request with a raw ValueError -> 500 error page. Raises ValueError
    with a friendly, field-specific message on anything still unparseable,
    which calling routes should catch and flash back to the user."""
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if text == "":
        return 0.0
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"\"{value}\" isn't a valid number for {field_label}. "
                          f"Please enter digits only (e.g. 1500 or 1500.50, no commas or currency symbols).")


def check_credit_block(customer_id, company, user):
    """Raises ValueError (caught the same way as any other Sale-form
    validation error) if the Admin-configured CreditControlMode says this
    customer's due should block a NEW sale. 'Informational' never blocks -
    only the client-side warning banner applies. An Admin account always
    bypasses any block and can save the sale regardless, matching how
    editing a Sale is already Admin-only elsewhere."""
    mode = company["CreditControlMode"] or "Informational"
    if mode == "Informational":
        return
    if user and user["Role"] == "Admin":
        return
    info = get_customer_due_info(customer_id)
    if info["due"] <= 0:
        return
    if mode == "BlockImmediate":
        raise ValueError(
            f"This customer has an outstanding balance of ₹{info['due']:.2f}. New sales are blocked "
            f"for any customer with a due balance (Complete Block mode, set in Company Settings). "
            f"An Admin account can save this sale to override.")
    if mode == "BlockAfterDays":
        days_limit = company["CreditBlockDays"] or 0
        if days_limit > 0 and info["days_overdue"] > days_limit:
            raise ValueError(
                f"This customer has an outstanding balance of ₹{info['due']:.2f} overdue for "
                f"{info['days_overdue']} days, past the {days_limit}-day limit set in Company Settings. "
                f"An Admin account can save this sale to override.")


def delete_record_custom_data(module, record_id):
    """Removes every CustomFieldValues row and any attachment-type custom
    field's uploaded files for one specific record of `module` - the
    counterpart to save_custom_fields()/get_custom_attachments(), used when
    the record itself (e.g. a Sale) is being permanently deleted so its
    custom-field values and files don't linger orphaned."""
    for d in get_custom_field_defs(module):
        if d["FieldType"] == "attachment":
            att_module = custom_field_attachment_module(d["FieldID"])
            for att in get_attachments(att_module, record_id):
                full_path = os.path.join(UPLOAD_ROOT, att["StoredPath"])
                try:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                except OSError:
                    pass
            db.execute("DELETE FROM Attachments WHERE ModuleName=? AND RecordID=?", (att_module, record_id))
        else:
            db.execute("DELETE FROM CustomFieldValues WHERE FieldID=? AND RecordID=?", (d["FieldID"], record_id))


# ---------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------


SALES_LIST_PAGE_SIZE = 100


@bp.route("/sales")
def sales_list():
    # Filtered in SQL (WHERE) and paginated (LIMIT/OFFSET) rather than fetching every
    # Sale ever recorded into Python on every visit to this page and filtering there -
    # that pattern (still used by several other list pages) doesn't scale: it gets
    # slower and heavier on every render as the table grows, forever, with no ceiling.
    # Sales specifically is the highest-value place to fix first since it's the
    # fastest-growing table in the app (a new row every sale, every day).
    filt = request.args.get("filter", "")
    # Optional quick filters (linked from the Live Sales Monitor report) - narrow to one
    # salesperson and/or one date without needing a dedicated search form here.
    employee_id = request.args.get("employee_id", type=int)
    date_filter = request.args.get("date", "")
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = ["1=1"]
    params = []
    if filt == "unassigned":
        where.append("c.IsUnassignedBucket = 1")
    if employee_id:
        where.append("s.EmployeeID = ?")
        params.append(employee_id)
    if date_filter:
        where.append("s.SaleDate = ?")
        params.append(date_filter)
    if q:
        where.append("""(s.InvoiceNumber LIKE ? OR c.CustomerName LIKE ? OR e.EmployeeName LIKE ?
                       OR s.Status LIKE ? OR s.PaymentStatus LIKE ?)""")
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    where_sql = " AND ".join(where)

    from_clause = """FROM Sales s JOIN Customers c ON c.CustomerID=s.CustomerID
                   LEFT JOIN Employees e ON e.EmployeeID=s.EmployeeID"""
    total = db.query(f"SELECT COUNT(*) c {from_clause} WHERE {where_sql}", params, one=True)["c"]
    total_pages = max((total + SALES_LIST_PAGE_SIZE - 1) // SALES_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * SALES_LIST_PAGE_SIZE

    sales = db.query(f"""SELECT s.*, c.CustomerName, c.IsUnassignedBucket, e.EmployeeName {from_clause}
                      WHERE {where_sql} ORDER BY s.SaleDate DESC, s.SaleID DESC LIMIT ? OFFSET ?""",
                     params + [SALES_LIST_PAGE_SIZE, offset])
    return render_template("sales_list.html", sales=sales, columns=get_effective_columns("Sale"), filt=filt,
                            employee_id=employee_id, date_filter=date_filter, q=q,
                            page=page, total_pages=total_pages, total=total, page_size=SALES_LIST_PAGE_SIZE)


@bp.route("/sales/reports/day-wise")
def sales_daywise_report():
    today = date.today()
    default_from = today.replace(day=1).isoformat()
    default_to = today.isoformat()
    date_from = request.args.get("from") or default_from
    date_to = request.args.get("to") or default_to

    # Total Sales is always the full GST-inclusive invoice value (for revenue
    # visibility). Balance Due, though, is based on what's actually
    # collectable from the customer: for a Reverse Charge sale that's the
    # taxable (product) value only, since the customer pays GST straight to
    # the government rather than to us - so "due" is computed per-sale via
    # the CASE below rather than off the SUM(TotalAmount) directly.
    rows = db.query("""SELECT SaleDate AS d, COUNT(*) AS invoice_count,
                     COALESCE(SUM(TotalAmount), 0) AS total,
                     COALESCE(SUM(CASE WHEN ReverseCharge THEN TaxableAmount ELSE TotalAmount END), 0) AS due_total,
                     COALESCE(SUM(AmountReceived), 0) AS received
                     FROM Sales WHERE Status<>'Cancelled' AND SaleDate BETWEEN ? AND ?
                     GROUP BY SaleDate ORDER BY SaleDate DESC""", (date_from, date_to))
    daywise = []
    grand_total = grand_due = grand_received = 0.0
    for r in rows:
        balance = round((r["due_total"] or 0) - (r["received"] or 0), 2)
        daywise.append({"date": r["d"], "invoice_count": r["invoice_count"],
                         "total": round(r["total"], 2), "received": round(r["received"], 2),
                         "balance": balance})
        grand_total += r["total"] or 0
        grand_due += r["due_total"] or 0
        grand_received += r["received"] or 0
    totals = {"total": round(grand_total, 2), "received": round(grand_received, 2),
              "balance": round(grand_due - grand_received, 2),
              "invoice_count": sum(d["invoice_count"] for d in daywise)}
    return render_template("sales_daywise_report.html", daywise=daywise, totals=totals,
                            date_from=date_from, date_to=date_to)


def sale_delete_impact(sid):
    """Everything that will actually change if this Sale is deleted right
    now - computed up front so the confirmation page can show the real
    consequences (not just a generic warning) before anyone commits to it."""
    stock_restored = db.query("""SELECT pr.ProductName, pr.Unit, -SUM(it.QtyChange) AS qty
                               FROM InventoryTransactions it JOIN Products pr ON pr.ProductID = it.ProductID
                               WHERE it.RefType='Sale' AND it.RefID=?
                               GROUP BY it.ProductID HAVING qty <> 0""", (sid,))
    issue_credit = db.query("""SELECT pr.ProductName, pr.Unit, ssl.QtyApplied AS qty, si.IssueID, si.IssueDate,
                             e.EmployeeName
                             FROM SaleStockIssueLinks ssl
                             JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                             JOIN StockIssues si ON si.IssueID = sil.IssueID
                             JOIN Products pr ON pr.ProductID = sil.ProductID
                             LEFT JOIN Employees e ON e.EmployeeID = si.EmployeeID
                             WHERE ssl.SaleID=?""", (sid,))
    reconciliation_linked = db.query("SELECT IssueID, IssueDate FROM StockIssues WHERE SaleID=?", (sid,), one=True)
    custom_field_count = db.query("""SELECT COUNT(*) c FROM CustomFieldValues v
                                   JOIN CustomFieldDefinitions d ON d.FieldID = v.FieldID
                                   WHERE d.ModuleName='Sale' AND v.RecordID=?""", (sid,), one=True)["c"]
    return {
        "stock_restored": stock_restored,
        "issue_credit": issue_credit,
        "reconciliation_linked": reconciliation_linked,
        "custom_field_count": custom_field_count,
    }


@bp.route("/sales/<int:sid>/delete", methods=["GET", "POST"])
@admin_required
def sale_delete(sid):
    # One atomic transaction for the whole POST (reads and writes), so overlapping requests serialize.
    if request.method == "POST":
        with db.transaction():
            return _sale_delete(sid)
    return _sale_delete(sid)


def _sale_delete(sid):
    """GET shows a confirmation page listing the actual, computed impact of
    deleting this Sale (stock that will be restored, any Stock Issue credit
    that will be reversed, whether it's reconciliation-linked) before asking
    'do you still want to continue'; POST is the one that actually deletes
    it. Fully reverses everything the Sale did: any warehouse stock it
    deducted directly, any Stock Issue Qty Sold it credited instead, and
    (if this is the auto-created 'Unassigned' sale from a reconciled Stock
    Issue) unlinks it from that issue first, the same way stock_issue_delete()
    does the reverse case. Admin-only and irreversible - there's no undo
    once this runs. Note this does NOT retroactively adjust any GST return
    or P&L figure already filed/shared outside the app for a period this
    sale fell in - both are computed live from the Sales table, so they'll
    simply reflect the sale's absence from now on."""
    sale = db.query("""SELECT s.*, c.CustomerName FROM Sales s
                     JOIN Customers c ON c.CustomerID = s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))

    locked_issue_id = locked_issue_for_sale(sid)
    if locked_issue_id:
        log_stock_issue_change(locked_issue_id, "Blocked Delete Attempt", f"Invoice {sale['InvoiceNumber']}",
                                None, "delete blocked - this Stock Issue is Reconciled")
        flash(locked_sale_message(locked_issue_id, "delete"), "error")
        return redirect(url_for("sale_view", sid=sid))

    if request.method == "GET":
        impact = sale_delete_impact(sid)
        return render_template("sale_delete_confirm.html", sale=sale, **impact)

    # A reason is required - typed on the confirmation page - so the audit trail below
    # answers not just what/who/when but why, without a separate follow-up question.
    reason = (request.form.get("delete_reason") or "").strip()
    if not reason:
        flash("Enter a reason for deleting this sale before continuing.", "error")
        impact = sale_delete_impact(sid)
        return render_template("sale_delete_confirm.html", sale=sale, delete_reason="", **impact)

    # Snapshot what this Sale looked like before it's gone for good - the only trace left
    # once the DELETEs below run, and what the 'Deleted Sales' report reads from.
    current_user = get_current_user()
    line_count = db.query("SELECT COUNT(*) c FROM SalesLines WHERE SaleID=?", (sid,), one=True)["c"]
    db.execute("""INSERT INTO DeletedSalesLog (SaleID, InvoiceNumber, CustomerName, SaleDate, EmployeeName,
                TotalAmount, LineCount, DeletedByUserID, DeletedByUsername, Reason)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (sid, sale["InvoiceNumber"], sale["CustomerName"], sale["SaleDate"],
                db.query("SELECT EmployeeName FROM Employees WHERE EmployeeID=?",
                         (sale["EmployeeID"],), one=True)["EmployeeName"] if sale["EmployeeID"] else None,
                sale["TotalAmount"] or 0, line_count,
                current_user["UserID"] if current_user else None,
                current_user["Username"] if current_user else None, reason))

    # Give back whatever this Sale credited toward a Stock Issue's Qty Sold,
    # and remove the link rows (mirrors the first step of an edit).
    reverse_sale_stock_issue_links(sid)
    # Reverse this Sale's own warehouse stock deduction, if any. Delete any
    # DirectSaleReviews rows for those transactions first - PRAGMA foreign_keys=ON
    # would otherwise block deleting the InventoryTransactions row they reference.
    db.execute("""DELETE FROM DirectSaleReviews WHERE TransactionID IN
                (SELECT TransactionID FROM InventoryTransactions WHERE RefType='Sale' AND RefID=?)""", (sid,))
    db.execute("DELETE FROM InventoryTransactions WHERE RefType='Sale' AND RefID=?", (sid,))
    # If this is the reconciliation-created 'Unassigned' sale a Stock Issue still
    # points at, unlink it first - StockIssues.SaleID has no ON DELETE CASCADE, so
    # deleting the Sale while it's still referenced would fail the foreign key check.
    db.execute("UPDATE StockIssues SET SaleID=NULL WHERE SaleID=?", (sid,))
    delete_record_custom_data("Sale", sid)
    db.execute("DELETE FROM SalesLines WHERE SaleID=?", (sid,))
    db.execute("DELETE FROM Sales WHERE SaleID=?", (sid,))
    flash(f"Sale {sale['InvoiceNumber']} deleted permanently, and its stock impact reversed.", "success")
    return redirect(url_for("sales_list"))


def sale_stock_issue_credit_message(sale_id, plain=False):
    """Friendly one-line summary of how much of this Sale (if any) got
    credited against a salesperson's open Stock Issue, for the post-save
    flash message - so it's obvious the app picked up the link rather than
    silently doing something invisible."""
    applied = db.query("""SELECT COUNT(*) c, COALESCE(SUM(QtyApplied),0) q FROM SaleStockIssueLinks
                        WHERE SaleID=?""", (sale_id,), one=True)
    if applied and applied["c"]:
        msg = f"{applied['q']:g} unit(s) credited against the salesperson's open Stock Issue for that day."
        return msg if plain else f"Sale recorded — {msg}"
    return "" if plain else "Sale recorded."


def resolve_sale_customer(f):
    """Handles the Sales form's editable Customer section: Name/Address/
    Phone/Zone are shown as plain text inputs pre-filled from whichever
    customer is picked in the dropdown, and are editable right there rather
    than requiring a trip to the Customers tab. If an existing customer_id
    was posted, this updates that customer's Name/Address/Phone/Zone in
    place from whatever the form now says (so a correction made while
    entering a sale sticks for next time too). If no customer_id was
    posted but a name was typed, a brand new Customer is created on the fly.
    Returns the CustomerID to use for this sale."""
    customer_id = f.get("customer_id") or ""
    name = (f.get("customer_name") or "").strip()
    address = (f.get("customer_address") or "").strip()
    phone = (f.get("customer_phone") or "").strip()
    zone = (f.get("customer_zone") or "").strip()
    if customer_id:
        cid = int(customer_id)
        if name:
            db.execute("UPDATE Customers SET CustomerName=?, Address=?, Phone=?, Zone=? WHERE CustomerID=?",
                       (name, address, phone, zone, cid))
        return cid
    if not name:
        raise ValueError("Customer name is required.")
    # No customer_id was posted (the "-- new customer --" option, or the search box was
    # typed into without picking a match). Before creating a new row, check for an existing
    # customer with the same name (case/whitespace-insensitive) and reuse it instead - this
    # is what was silently creating duplicate customer records (e.g. two "Sai phani kirana"
    # rows) whenever someone typed a name that already existed rather than selecting it from
    # the dropdown/search box.
    existing = db.query(
        "SELECT CustomerID FROM Customers WHERE LOWER(TRIM(CustomerName))=LOWER(TRIM(?)) AND IsUnassignedBucket=0",
        (name,), one=True)
    if existing:
        cid = existing["CustomerID"]
        db.execute("UPDATE Customers SET CustomerName=?, Address=COALESCE(NULLIF(?,''), Address), "
                   "Phone=COALESCE(NULLIF(?,''), Phone), Zone=COALESCE(NULLIF(?,''), Zone) WHERE CustomerID=?",
                   (name, address, phone, zone, cid))
        return cid
    return db.execute("""INSERT INTO Customers (CustomerName, Address, Phone, Zone, Active, AccountNumber)
                       VALUES (?,?,?,?,1,?)""", (name, address, phone, zone, next_customer_account_number()))


def locked_salesperson_employee_id():
    """If the signed-in user is a Staff/Supervisor linked to an Employee
    record, their Salesperson field on the Sales form is auto-picked and
    LOCKED to themselves (returns that EmployeeID). Manager/Admin (and any
    Staff/Supervisor not yet linked to an Employee) keep the normal manual
    dropdown (returns None)."""
    user = get_current_user()
    if user and user["Role"] in ("Staff", "Supervisor") and user["EmployeeID"]:
        return user["EmployeeID"]
    return None


@bp.route("/sales/new", methods=["GET", "POST"])
def sale_form():
    company = get_company_settings()
    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        discounts = request.form.getlist("discount_amount[]")
        if len(discounts) < len(product_ids):
            discounts = discounts + ["0"] * (len(product_ids) - len(discounts))

        try:
            lines = [(int(p), parse_form_number(q, "Qty"), parse_form_number(pr, "Rate"),
                      parse_form_number(d, "Discount ₹"))
                     for p, q, pr, d in zip(product_ids, qtys, prices, discounts) if p and q]
            cash_amount = parse_form_number(f.get("cash_amount") or "0", "Cash Amount")
            bank_amount = parse_form_number(f.get("bank_amount") or "0", "Bank Amount")
            amount_received = round(cash_amount + bank_amount, 2)
            customer_id = resolve_sale_customer(f)
            check_credit_block(customer_id, company, get_current_user())
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("sale_form"))

        place_of_supply_code = f.get("place_of_supply_code", "") or company["StateCode"]
        locked_employee_id = locked_salesperson_employee_id()
        employee_id = locked_employee_id or (int(f["employee_id"]) if f.get("employee_id") else None)

        sale_id = create_sale(
            customer_id=customer_id, sale_date=f["sale_date"], status=f["status"],
            payment_status=f["payment_status"], payment_due_date=f.get("payment_due_date"),
            amount_received=amount_received, notes=f.get("notes", ""),
            place_of_supply_code=place_of_supply_code, lines=lines,
            reverse_charge=bool(f.get("reverse_charge")), invoice_no=f.get("invoice_number") or None,
            employee_id=employee_id, cash_amount=cash_amount, bank_amount=bank_amount,
            auto_create_issue=bool(locked_employee_id))
        save_custom_fields("Sale", sale_id, f)
        log_sale_location(sale_id, get_current_user(), f.get("sale_lat"), f.get("sale_lng"), f.get("sale_accuracy"))
        flash(sale_stock_issue_credit_message(sale_id), "success")
        # Show a "Saved! Invoice #X" popup for every save (Admin/Office/Sales rep alike),
        # then default straight to the 58mm thermal receipt on OK - see
        # sale_invoice_thermal()/invoice_thermal.html for the popup + print wiring.
        # A link on that page still lets anyone switch to the full A4 Tax Invoice.
        return redirect(url_for("sale_invoice_thermal", sid=sale_id, saved=1))
    customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
    products = get_products_with_stock()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    custom_fields = get_custom_field_defs("Sale")
    locked_employee_id = locked_salesperson_employee_id()
    locked_employee = None
    if locked_employee_id:
        locked_employee = db.query("SELECT * FROM Employees WHERE EmployeeID=?", (locked_employee_id,), one=True)
    current_user_row = get_current_user()
    show_salesperson_warning = bool(current_user_row and current_user_row["Role"] in ("Staff", "Supervisor")
                                     and not locked_employee_id)
    zones = db.query("SELECT * FROM Zones WHERE Active=1 ORDER BY DisplayOrder, ZoneID")
    return render_template("sale_form.html", customers=customers, products=products, employees=employees,
                            today=today_str(), states=INDIAN_STATES, company=company, suggested_invoice=None,
                            custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={}, sale=None, existing_lines=None,
                            current_customer=None, locked_employee=locked_employee,
                            show_salesperson_warning=show_salesperson_warning, zones=zones,
                            header_fields={f["key"]: f for f in get_effective_form_fields("SaleFormHeader")},
                            line_fields={f["key"]: f for f in get_effective_form_fields("SaleFormLine")})


def _sale_inventory_unchanged(existing, status, employee_id, sale_date, lines):
    """True if a submitted sale edit leaves every inventory-affecting field as stored: status,
    salesperson, date, and each line's product/qty/rate/total discount (lines carry a per-unit
    discount, stored lines a per-line total)."""
    if (existing["Status"], existing["EmployeeID"], existing["SaleDate"]) != (status, employee_id, sale_date):
        return False
    stored = sorted((r["ProductID"], round(r["Qty"], 4), round(r["UnitPrice"], 2), round(r["DiscountAmount"] or 0, 2))
                    for r in db.query("SELECT ProductID, Qty, UnitPrice, DiscountAmount FROM SalesLines WHERE SaleID=?",
                                      (existing["SaleID"],)))
    submitted = sorted((p, round(q, 4), round(pr, 2), round(d * q, 2)) for p, q, pr, d in lines)
    return stored == submitted


@bp.route("/sales/<int:sid>/edit", methods=["GET", "POST"])
@admin_required
def sale_edit(sid):
    """Admin-only: correct a mistake on an already-saved sale without
    deleting and re-entering it. The Invoice Number is preserved."""
    company = get_company_settings()
    existing = db.query("SELECT * FROM Sales WHERE SaleID=?", (sid,), one=True)
    if not existing:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        prices = request.form.getlist("unit_price[]")
        discounts = request.form.getlist("discount_amount[]")
        if len(discounts) < len(product_ids):
            discounts = discounts + ["0"] * (len(product_ids) - len(discounts))

        try:
            lines = [(int(p), parse_form_number(q, "Qty"), parse_form_number(pr, "Rate"),
                      parse_form_number(d, "Discount ₹"))
                     for p, q, pr, d in zip(product_ids, qtys, prices, discounts) if p and q]
            cash_amount = parse_form_number(f.get("cash_amount") or "0", "Cash Amount")
            bank_amount = parse_form_number(f.get("bank_amount") or "0", "Bank Amount")
            amount_received = round(cash_amount + bank_amount, 2)
            customer_id = resolve_sale_customer(f)
            check_credit_block(customer_id, company, get_current_user())
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("sale_edit", sid=sid))

        place_of_supply_code = f.get("place_of_supply_code", "") or company["StateCode"]
        employee_id = int(f["employee_id"]) if f.get("employee_id") else None

        try:
            locked_issue_id = locked_issue_for_sale(sid)
            if locked_issue_id and _sale_inventory_unchanged(existing, f["status"], employee_id, f["sale_date"], lines):
                # Locked by a Reconciled Stock Issue, but nothing inventory-affecting is changing:
                # allow only the safe metadata (Notes, Due Date) and reject anything else.
                if (existing["CustomerID"] != customer_id or existing["PaymentStatus"] != f["payment_status"]
                        or round(existing["CashAmount"] or 0, 2) != round(cash_amount, 2)
                        or round(existing["BankAmount"] or 0, 2) != round(bank_amount, 2)
                        or (existing["PlaceOfSupplyStateCode"] or "") != (place_of_supply_code or "")
                        or bool(existing["ReverseCharge"]) != bool(f.get("reverse_charge"))):
                    raise SaleStockIssueLockedError(locked_issue_id)
                with db.transaction():
                    db.execute("UPDATE Sales SET Notes=?, PaymentDueDate=? WHERE SaleID=?",
                               (f.get("notes", ""), f.get("payment_due_date") or None, sid))
                    log_stock_issue_change(locked_issue_id, "Sale Header Edited", f"Invoice {existing['InvoiceNumber']}",
                                            None, "notes / due date updated")
            else:
                create_sale(
                    customer_id=customer_id, sale_date=f["sale_date"], status=f["status"],
                    payment_status=f["payment_status"], payment_due_date=f.get("payment_due_date"),
                    amount_received=amount_received, notes=f.get("notes", ""),
                    place_of_supply_code=place_of_supply_code, lines=lines,
                    reverse_charge=bool(f.get("reverse_charge")), invoice_no=existing["InvoiceNumber"], sale_id=sid,
                    employee_id=employee_id, cash_amount=cash_amount, bank_amount=bank_amount)
        except SaleStockIssueLockedError as e:
            log_stock_issue_change(e.issue_id, "Blocked Edit Attempt", f"Invoice {existing['InvoiceNumber']}",
                                    None, "edit blocked - this Stock Issue is Reconciled")
            flash(locked_sale_message(e.issue_id, "edit"), "error")
            return redirect(url_for("sale_edit", sid=sid))
        save_custom_fields("Sale", sid, f)
        log_sale_location(sid, get_current_user(), f.get("sale_lat"), f.get("sale_lng"), f.get("sale_accuracy"))
        credit_note = sale_stock_issue_credit_message(sid, plain=True)
        flash(f"Sale {existing['InvoiceNumber']} updated." + (f" {credit_note}" if credit_note else ""), "success")
        return redirect(url_for("sale_view", sid=sid))

    customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
    products = get_products_with_stock()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    existing_lines = db.query("SELECT * FROM SalesLines WHERE SaleID=?", (sid,))
    custom_fields = get_custom_field_defs("Sale")
    custom_values = get_custom_values("Sale", sid)
    current_customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (existing["CustomerID"],), one=True)
    zones = db.query("SELECT * FROM Zones WHERE Active=1 ORDER BY DisplayOrder, ZoneID")
    if current_customer and current_customer["Zone"] and current_customer["Zone"] not in {z["ZoneName"] for z in zones}:
        zones = list(zones) + [{"ZoneID": None, "ZoneName": current_customer["Zone"]}]
    return render_template("sale_form.html", customers=customers, products=products, employees=employees,
                            today=existing["SaleDate"], states=INDIAN_STATES, company=company, suggested_invoice=None,
                            custom_fields=custom_fields, custom_values=custom_values, cf_record_id=sid,
                            custom_attachments={}, sale=existing, existing_lines=existing_lines,
                            current_customer=current_customer, locked_employee=None,
                            show_salesperson_warning=False, zones=zones,
                            header_fields={f["key"]: f for f in get_effective_form_fields("SaleFormHeader")},
                            line_fields={f["key"]: f for f in get_effective_form_fields("SaleFormLine")})


# ---------------------------------------------------------------------
# Sales: bulk Excel upload
#
# Off an app-provided template (download it, fill it in, upload it).
# Two-step flow, same shape as the Purchases vendor-file import: upload +
# parse + auto-match Customer/Product by exact name, review/fix any
# unmatched rows and edit per-invoice header fields, then confirm to
# actually create the Sale(s) via the same create_sale() every other
# entry point uses - so GST and stock are computed identically.
# ---------------------------------------------------------------------


@bp.route("/sales/import/template")
def sales_import_template():
    from flask import send_file
    buf = bulk_import.build_sales_template()
    return send_file(buf, as_attachment=True, download_name="Sales_Upload_Template.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/sales/import", methods=["GET", "POST"])
def sales_import_upload():
    if request.method == "POST":
        file_storage = request.files.get("file")
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("sales_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = bulk_import.parse_sales_excel(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("sales_import_upload"))

        rows = result["rows"]
        # Group by Invoice Number - a blank invoice number gets its own
        # group per row (each becomes its own single-line sale), same as
        # purchase_import_upload() treats a blank SO Number.
        group_index_by_key = {}
        groups = []
        for i, row in enumerate(rows):
            key = row["invoice_number"] or f"__blank_{i}__"
            if key not in group_index_by_key:
                group_index_by_key[key] = len(groups)
                groups.append(dict(invoice_number=row["invoice_number"], dates=[], rows=[]))
            g = groups[group_index_by_key[key]]
            g["rows"].append(row)
            if row["date"]:
                g["dates"].append(row["date"])

        for g in groups:
            g["sale_date"] = max(g["dates"]) if g["dates"] else today_str()
            g["payment_status"] = g["rows"][0]["payment_status"]
            g["amount_received"] = sum(r["amount_received"] for r in g["rows"]) or g["rows"][0]["amount_received"]

        customers = db.query("SELECT * FROM Customers WHERE Active=1 ORDER BY CustomerName")
        products = get_products_with_stock()
        customer_by_name = {c["CustomerName"].strip().lower(): c for c in customers}
        product_by_name = {p["ProductName"].strip().lower(): p for p in products}

        for i, row in enumerate(rows):
            row["global_index"] = i
            row["group_index"] = group_index_by_key[row["invoice_number"] or f"__blank_{i}__"]
            row["customer_match"] = customer_by_name.get(row["customer_name"].strip().lower())
            row["product_match"] = product_by_name.get(row["product"].strip().lower())

        matched_cust = sum(1 for r in rows if r["customer_match"])
        matched_prod = sum(1 for r in rows if r["product_match"])
        flash(f"{result['message']} {matched_cust}/{len(rows)} customers matched, "
              f"{matched_prod}/{len(rows)} products matched — review below before confirming.", "success")
        return render_template("sales_import_review.html", groups=groups, rows=rows, customers=customers,
                                products=products, today=today_str())

    return render_template("sales_import_upload.html")


@bp.route("/sales/import/confirm", methods=["POST"])
def sales_import_confirm():
    f = request.form
    company = get_company_settings()
    row_count = int(f.get("row_count") or 0)
    group_count = int(f.get("group_count") or 0)

    group_lines = [[] for _ in range(group_count)]
    group_info = []
    for g in range(group_count):
        group_info.append(dict(
            invoice_number=(f.get(f"invoice_number_{g}") or "").strip() or None,
            sale_date=f.get(f"sale_date_{g}") or today_str(),
            payment_status=f.get(f"payment_status_{g}") or "Unpaid",
            amount_received=float(f.get(f"amount_received_{g}") or 0),
        ))

    created_sale_ids = []
    skipped = 0
    for i in range(row_count):
        group_idx = int(f.get(f"row_group_{i}", 0))
        qty = float(f.get(f"row_qty_{i}") or 0)
        rate = float(f.get(f"row_rate_{i}") or 0)
        if qty <= 0:
            skipped += 1
            continue

        product_id_raw = f.get(f"row_product_id_{i}", "")
        if not product_id_raw or product_id_raw == "__skip__":
            skipped += 1
            continue
        product_id = int(product_id_raw)

        group_lines[group_idx].append((product_id, qty, rate))

    # Resolve (and, for "__new__", actually create) each group's customer
    # only now that we know the group has at least one usable line - so a
    # group whose only line(s) got skipped for having no product match
    # doesn't leave behind an unused new Customer row.
    for g, lines in enumerate(group_lines):
        if not lines:
            continue
        customer_id_raw = f.get(f"customer_id_{g}", "")
        customer_id = None
        if customer_id_raw == "__new__":
            new_name = (f.get(f"new_customer_name_{g}") or "").strip()
            if new_name:
                customer_id = db.execute("INSERT INTO Customers (CustomerName, Active, AccountNumber) VALUES (?,1,?)",
                                          (new_name, next_customer_account_number()))
        elif customer_id_raw and customer_id_raw != "__skip__":
            customer_id = int(customer_id_raw)
        if not customer_id:
            continue

        info = group_info[g]
        customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (customer_id,), one=True)
        place_of_supply_code = (customer["StateCode"] if customer else "") or company["StateCode"]
        sale_id = create_sale(
            customer_id=customer_id, sale_date=info["sale_date"], status="Completed",
            payment_status=info["payment_status"], payment_due_date=None,
            amount_received=info["amount_received"], notes="Imported from Excel upload",
            place_of_supply_code=place_of_supply_code, lines=lines, invoice_no=info["invoice_number"])
        created_sale_ids.append(sale_id)

    if not created_sale_ids:
        flash("Nothing to import — no group had both a matched customer and at least one usable line.", "error")
        return redirect(url_for("sales_import_upload"))

    msg = f"Imported {len(created_sale_ids)} sale(s) from the file."
    if skipped:
        msg += f" ({skipped} line(s) skipped — no product/customer match or zero quantity.)"
    flash(msg, "success")
    if len(created_sale_ids) == 1:
        return redirect(url_for("sale_view", sid=created_sale_ids[0]))
    return redirect(url_for("sales_list"))


@bp.route("/sales/<int:sid>")
def sale_view(sid):
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.Zone, c.IsUnassignedBucket,
                     e.EmployeeName FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID
                     LEFT JOIN Employees e ON e.EmployeeID=s.EmployeeID WHERE s.SaleID=?""", (sid,), one=True)
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    custom_fields = get_custom_field_defs("Sale")
    custom_values = get_custom_values("Sale", sid)
    balance_due = sale_balance_due(sale)
    stock_issue_credit = db.query("""SELECT COUNT(*) c, COALESCE(SUM(QtyApplied),0) q, si.IssueID
                                   FROM SaleStockIssueLinks ssl
                                   JOIN StockIssueLines sil ON sil.LineID = ssl.StockIssueLineID
                                   JOIN StockIssues si ON si.IssueID = sil.IssueID
                                   WHERE ssl.SaleID=?""", (sid,), one=True)
    payments = db.query("""SELECT * FROM SalePayments WHERE SaleID=? ORDER BY PaymentDate, PaymentID""", (sid,))
    # Any line on this sale that couldn't be fully covered by an open (or
    # capacity-remaining reconciled) Stock Issue gets posted as a direct
    # warehouse deduction and parked in DirectSaleReviews for Admin review
    # (see create_sale()). That queue lives under Inventory > Direct Sale
    # Review, which is easy to miss - surface it right here on the sale
    # itself so it isn't silently invisible.
    direct_sale_reviews = db.query("""SELECT dsr.*, p.ProductName, p.Unit, -it.QtyChange AS Qty
                                    FROM DirectSaleReviews dsr
                                    JOIN InventoryTransactions it ON it.TransactionID = dsr.TransactionID
                                    JOIN Products p ON p.ProductID = dsr.ProductID
                                    WHERE it.RefType='Sale' AND it.RefID=?
                                    ORDER BY dsr.ReviewID""", (sid,))
    return render_template("sale_view.html", sale=sale, lines=lines, balance_due=balance_due,
                            payments=payments, today=today_str(),
                            stock_issue_credit=stock_issue_credit if stock_issue_credit and stock_issue_credit["c"] else None,
                            direct_sale_reviews=direct_sale_reviews,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=sid, custom_attachments=get_custom_attachments("Sale", sid))


@bp.route("/sales/<int:sid>/record-payment", methods=["POST"])
def sale_record_payment(sid):
    # One atomic transaction for the whole POST (reads and writes), so overlapping requests serialize.
    if request.method == "POST":
        with db.transaction():
            return _sale_record_payment(sid)
    return _sale_record_payment(sid)


def _sale_record_payment(sid):
    """Log one payment received against a specific invoice's outstanding
    balance (Accounts Receivable / Sale detail page "Record Payment"). Adds a
    dated SalePayments row (building up a full audit trail / customer ledger)
    and rolls the total into Sales.AmountReceived + PaymentStatus, same as
    stock_issue_collect_due does for van-sale dues."""
    sale = db.query("SELECT * FROM Sales WHERE SaleID=?", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    f = request.form
    next_url = f.get("next") or ""
    redirect_to = next_url if next_url.startswith("/") and not next_url.startswith("//") else url_for("sale_view", sid=sid)
    try:
        amount = round(float(f.get("amount") or 0), 2)
    except ValueError:
        amount = 0
    if amount <= 0:
        flash("Enter a payment amount greater than zero.", "error")
        return redirect(redirect_to)
    balance = sale_balance_due(sale)
    if balance <= 0.005:
        flash("This invoice is already fully paid.", "error")
        return redirect(redirect_to)
    if amount - balance > 0.005:
        flash(f"That's more than the balance due (₹{balance:.2f}) — recorded ₹{balance:.2f} instead.", "warning")
        amount = balance
    payment_date = f.get("payment_date") or today_str()
    method = (f.get("method") or "Cash").strip() or "Cash"
    notes = (f.get("notes") or "").strip()
    db.execute("""INSERT INTO SalePayments (SaleID, PaymentDate, Amount, PaymentMethod, Notes, CreatedAt)
                VALUES (?,?,?,?,?,?)""",
               (sid, payment_date, amount, method, notes, datetime.now().isoformat(timespec="seconds")))
    new_received = round((sale["AmountReceived"] or 0) + amount, 2)
    new_due_amount = sale_due_amount(sale)
    new_balance = round(new_due_amount - new_received, 2)
    if new_balance <= 0.005:
        payment_status = "Paid"
    elif new_received > 0.005:
        payment_status = "Partial"
    else:
        payment_status = "Unpaid"
    db.execute("UPDATE Sales SET AmountReceived=?, PaymentStatus=? WHERE SaleID=?",
               (new_received, payment_status, sid))
    flash(f"Recorded ₹{amount:.2f} payment for Invoice #{sale['InvoiceNumber']}. "
          f"{'Fully paid.' if new_balance <= 0.005 else f'₹{max(new_balance, 0):.2f} still due.'}", "success")
    return redirect(redirect_to)


@bp.route("/sales/<int:sid>/reassign", methods=["GET", "POST"])
def sale_reassign(sid):
    # One atomic transaction for the whole POST (reads and writes), so overlapping requests serialize.
    if request.method == "POST":
        with db.transaction():
            return _sale_reassign(sid)
    return _sale_reassign(sid)


def _sale_reassign(sid):
    """Split an 'Unassigned' Sale's lines out to the real customer(s). Each
    line's quantity can itself be broken down across multiple customers (e.g.
    20 units of a product sold that day split 8/7/5 across three customers) -
    each split names a quantity plus an existing Customer or a brand-new one
    typed directly on this page (no separate Add Customer trip needed).
    Splits going to the same new-customer details in one submission are
    grouped into a single new Customer + Sale. Any quantity not covered by a
    split stays behind on the original Sale."""
    sale = db.query("SELECT s.*, c.IsUnassignedBucket FROM Sales s JOIN Customers c ON c.CustomerID=s.CustomerID "
                     "WHERE s.SaleID=?", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    if not sale["IsUnassignedBucket"]:
        flash("This sale is already assigned to a customer.", "error")
        return redirect(url_for("sale_view", sid=sid))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))

    if request.method == "POST":
        f = request.form
        company = get_company_settings()
        groups = {}       # target_key -> {"lines": [...], "choice": str, "new_info": {...}|None}
        group_order = []
        keep_lines = []   # remaining (unsplit) quantity, left on the original Unassigned sale
        for line in lines:
            lid = line["LineID"]
            choices = f.getlist(f"split_choice_{lid}[]")
            qtys = f.getlist(f"split_qty_{lid}[]")
            new_names = f.getlist(f"split_new_name_{lid}[]")
            new_phones = f.getlist(f"split_new_phone_{lid}[]")
            new_addresses = f.getlist(f"split_new_address_{lid}[]")
            new_gstins = f.getlist(f"split_new_gstin_{lid}[]")
            new_states = f.getlist(f"split_new_state_{lid}[]")
            allocated = 0.0
            for i, choice in enumerate(choices):
                if not choice or choice == "keep":
                    continue
                try:
                    qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 0.0
                except ValueError:
                    qty = 0.0
                if qty <= 0:
                    continue
                new_info = None
                if choice.startswith("existing:"):
                    key = choice
                else:
                    new_name = (new_names[i] if i < len(new_names) else "").strip()
                    if not new_name:
                        continue
                    new_phone = (new_phones[i] if i < len(new_phones) else "").strip()
                    key = f"new:{new_name.lower()}:{new_phone}"
                    new_info = dict(
                        name=new_name, phone=new_phone,
                        address=(new_addresses[i] if i < len(new_addresses) else "").strip(),
                        gstin=(new_gstins[i] if i < len(new_gstins) else "").strip(),
                        state_code=(new_states[i] if i < len(new_states) else "").strip())
                # Cap this split at whatever's left unallocated on the line, so a
                # typo/over-entry can't allocate more than was actually sold.
                qty = min(qty, max(line["Qty"] - allocated, 0))
                if qty <= 0:
                    continue
                allocated = round(allocated + qty, 4)
                if key not in groups:
                    group_order.append(key)
                    groups[key] = dict(lines=[], choice=choice, new_info=new_info)
                groups[key]["lines"].append((line["ProductID"], qty, line["UnitPrice"]))
            remaining = round(line["Qty"] - allocated, 4)
            if remaining > 0:
                keep_lines.append((line["ProductID"], remaining, line["UnitPrice"]))

        new_sale_ids = []
        for key in group_order:
            g = groups[key]
            if g["choice"].startswith("existing:"):
                customer_id = int(g["choice"].split(":", 1)[1])
            else:
                ni = g["new_info"]
                customer_id = db.execute(
                    """INSERT INTO Customers (CustomerName, Phone, Address, GSTIN, State, StateCode, Active, AccountNumber)
                       VALUES (?,?,?,?,?,?,1,?)""",
                    (ni["name"], ni["phone"], ni["address"], ni["gstin"],
                     STATE_NAME_BY_CODE.get(ni["state_code"], ""), ni["state_code"], next_customer_account_number()))
            customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (customer_id,), one=True)
            place_of_supply_code = (customer["StateCode"] if customer else "") or company["StateCode"]
            new_sale_id = create_sale(
                customer_id=customer_id, sale_date=sale["SaleDate"], status="Completed",
                payment_status=sale["PaymentStatus"], payment_due_date=None, amount_received=0,
                notes=f"Reassigned from Unassigned Sale #{sid}", place_of_supply_code=place_of_supply_code,
                lines=g["lines"], post_inventory=False)
            new_sale_ids.append(new_sale_id)

        if not new_sale_ids:
            flash("Nothing reassigned — enter a quantity and pick a customer (existing or new) for at least one split.", "error")
            return redirect(url_for("sale_reassign", sid=sid))

        if keep_lines:
            create_sale(customer_id=sale["CustomerID"], sale_date=sale["SaleDate"], status=sale["Status"],
                        payment_status=sale["PaymentStatus"], payment_due_date=sale["PaymentDueDate"],
                        amount_received=sale["AmountReceived"], notes=sale["Notes"],
                        place_of_supply_code=sale["PlaceOfSupplyStateCode"], lines=keep_lines,
                        invoice_no=sale["InvoiceNumber"], sale_id=sid, post_inventory=False)
            flash(f"Reassigned {len(new_sale_ids)} customer group(s); "
                  f"{len(keep_lines)} line(s) left unassigned on this sale.", "success")
        else:
            # Every line was reassigned - the original Unassigned sale has nothing left to bill.
            db.execute("UPDATE StockIssues SET SaleID=NULL WHERE SaleID=?", (sid,))
            db.execute("DELETE FROM SalesLines WHERE SaleID=?", (sid,))
            db.execute("DELETE FROM Sales WHERE SaleID=?", (sid,))
            flash(f"Reassigned all lines to {len(new_sale_ids)} customer group(s). "
                  f"The Unassigned sale has been removed.", "success")
        if len(new_sale_ids) == 1 and not keep_lines:
            return redirect(url_for("sale_view", sid=new_sale_ids[0]))
        return redirect(url_for("sales_list"))

    customers = db.query("SELECT * FROM Customers WHERE Active=1 AND IsUnassignedBucket=0 ORDER BY CustomerName")
    return render_template("sale_reassign.html", sale=sale, lines=lines, customers=customers,
                            indian_states=INDIAN_STATES)


@bp.route("/sales/<int:sid>/invoice")
def sale_invoice(sid):
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.Zone, c.GSTIN AS CustomerGSTIN,
                     c.State AS CustomerState, c.StateCode AS CustomerStateCode, c.AccountNumber AS CustomerAccountNumber
                     FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    company = get_company_settings()
    words = amount_in_words(sale["TotalAmount"])
    balance_due = sale_balance_due(sale)
    auto_print = request.args.get("auto_print") == "1"
    return render_template("invoice.html", sale=sale, lines=lines, company=company, amount_words=words,
                            balance_due=balance_due, auto_print=auto_print)


@bp.route("/sales/<int:sid>/invoice/thermal")
def sale_invoice_thermal(sid):
    """A compact receipt formatted for a 58mm thermal roll printer (like a
    till/POS receipt) - an alternative to the full A4 Tax Invoice for
    counter/van sales where a small paper receipt is handed over on the
    spot. Shows each line's SKU, Qty, and net Rate (after any per-line
    discount), with GST summarized as a single block at the bottom rather
    than a per-line column (there isn't room for one on 58mm paper)."""
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.Zone, e.EmployeeName FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID
                     LEFT JOIN Employees e ON e.EmployeeID=s.EmployeeID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit, pr.SKU FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    company = get_company_settings()
    balance_due = sale_balance_due(sale)
    auto_print = request.args.get("auto_print") == "1"
    # "saved=1" (only set by sale_form()'s locked salesperson entry point right after a Save)
    # shows a "Saved! Invoice #X" popup first; printing then waits for that popup's OK button
    # instead of firing immediately on page load like a plain reprint (auto_print alone) does.
    saved = request.args.get("saved") == "1"
    return render_template("invoice_thermal.html", sale=sale, lines=lines, company=company,
                            balance_due=balance_due, auto_print=auto_print, saved=saved)


@bp.route("/sales/<int:sid>/invoice.pdf")
def sale_invoice_pdf(sid):
    from invoice_pdf import build_invoice_pdf
    sale = db.query("""SELECT s.*, c.CustomerName, c.Phone, c.Address, c.Zone, c.GSTIN AS CustomerGSTIN,
                     c.State AS CustomerState, c.StateCode AS CustomerStateCode, c.AccountNumber AS CustomerAccountNumber
                     FROM Sales s
                     JOIN Customers c ON c.CustomerID=s.CustomerID WHERE s.SaleID=?""", (sid,), one=True)
    if not sale:
        flash("Sale not found.", "error")
        return redirect(url_for("sales_list"))
    lines = db.query("""SELECT sl.*, pr.ProductName, pr.Unit FROM SalesLines sl
                      JOIN Products pr ON pr.ProductID=sl.ProductID WHERE sl.SaleID=?""", (sid,))
    company = get_company_settings()
    words = amount_in_words(sale["TotalAmount"])
    pdf_bytes = build_invoice_pdf(sale, lines, company, words)
    from flask import Response
    safe_name = sale["InvoiceNumber"].replace("/", "-")
    return Response(pdf_bytes, mimetype="application/pdf",
                     headers={"Content-Disposition": f'inline; filename="Invoice-{safe_name}.pdf"'})


@bp.route("/sales/<int:sid>/upi-qr.png")
def sale_upi_qr(sid):
    """Scannable UPI payment QR for this sale's invoice/receipt (invoice.html,
    invoice_thermal.html), generated on the fly from Company / GST Settings'
    UPI ID - not stored anywhere. Pre-fills the still-outstanding balance so a
    scan goes straight to "pay ₹X", or no amount if the sale's already paid
    up (the customer may still want to scan to pay something else/in
    advance). 404s whenever there's nothing to show (no UPI ID configured,
    or sale not found) - callers only use this inside an <img> tag, so a
    broken-image icon is an acceptable, non-blocking "nothing configured"."""
    from flask import Response, abort
    from upi_qr import build_upi_qr_png
    sale = db.query("SELECT * FROM Sales WHERE SaleID=?", (sid,), one=True)
    if not sale:
        abort(404)
    company = get_company_settings()
    if not company["UpiId"]:
        abort(404)
    balance_due = max(sale_balance_due(sale), 0)
    png = build_upi_qr_png(company["UpiId"], company["CompanyName"], balance_due or None,
                            f"Invoice {sale['InvoiceNumber']}")
    return Response(png, mimetype="image/png", headers={"Cache-Control": "no-store"})


def log_sale_location(sale_id, user, lat_raw, lng_raw, accuracy_raw):
    """Best-effort: records the device's location at the moment a Sale was
    saved, tagged to that Sale (LocationLogs.RefType='Sale', RefID=sale_id)
    - shown on the Location Tracking map as its own marker. Unlike the
    periodic ping (api_location_ping()), this applies to EVERY role,
    including Admin, and isn't gated by working hours/days or the tracked-
    role consent flow - it's metadata on a specific business record, not
    ongoing personal tracking. Silently does nothing if lat/lng weren't
    provided (geolocation denied/unavailable/unsupported, or it simply
    hadn't resolved yet by the time the form was submitted) - this must
    never raise or block the sale that was just saved."""
    if not user or lat_raw in (None, "") or lng_raw in (None, ""):
        return
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return
    try:
        accuracy = float(accuracy_raw) if accuracy_raw not in (None, "") else None
    except (TypeError, ValueError):
        accuracy = None
    now = _now_ist()
    db.execute(
        "INSERT INTO LocationLogs (UserID, EmployeeID, Role, Latitude, Longitude, Accuracy, RecordedAt, "
        "RecordedDate, RefType, RefID) VALUES (?,?,?,?,?,?,?,?,'Sale',?)",
        (user["UserID"], user["EmployeeID"], user["Role"], lat, lng, accuracy,
         now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"), sale_id))


# ---------------------------------------------------------------------
# Targets - sales targets to drive the distributor's business, set per
# Employee (required) x Product (optional - NULL applies across all
# products for that employee) x calendar Month. Actuals are always
# computed live from Stock Issues / StockIssueLines, never stored, so a
# target never goes stale relative to reconciliation edits. Week/day/
# MTD/WTD views are all derived from the month-level target by pro-
# rating across the days in that month.
# ---------------------------------------------------------------------


@bp.route("/targets")
def targets_view():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    selected_employee_id = request.args.get("employee_id", type=int)
    start, end, days_in_month = target_month_bounds(year, month)
    days_elapsed = target_days_elapsed(year, month)
    is_current_month = (year, month) == (today.year, today.month)

    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    is_admin = bool(get_current_user() and get_current_user()["Role"] == "Admin")

    q = """SELECT t.*, e.EmployeeName, p.ProductName FROM Targets t
           JOIN Employees e ON e.EmployeeID = t.EmployeeID
           LEFT JOIN Products p ON p.ProductID = t.ProductID
           WHERE t.TargetYear=? AND t.TargetMonth=?"""
    params = [year, month]
    if selected_employee_id:
        q += " AND t.EmployeeID=?"
        params.append(selected_employee_id)
    q += " ORDER BY e.EmployeeName, p.ProductName IS NULL DESC, p.ProductName"
    targets_raw = db.query(q, tuple(params))
    targets = [build_target_view(t, days_elapsed, days_in_month) for t in targets_raw]

    # Whole-company (or whole-employee, if one is selected) summary: only the
    # whole-employee (ProductID IS NULL) targets are added up, so a
    # product-level target under the same employee doesn't get double-counted.
    company_targets = [t for t in targets if t["ProductID"] is None]
    summary = {
        "qty_target": sum(t["QtySoldTarget"] for t in company_targets),
        "qty_actual_mtd": sum(t["mtd"]["qty_sold"] for t in company_targets),
        "qty_prorated": sum(t["prorated_qty"] for t in company_targets),
        "value_target": sum(t["SalesValueTarget"] for t in company_targets),
        "expected_mtd": sum(t["mtd"]["expected"] for t in company_targets),
        "collected_mtd": sum(t["mtd"]["collected"] for t in company_targets),
        "value_prorated": sum(t["prorated_value"] for t in company_targets),
        "discount_cap": sum(t["DiscountCapAmount"] for t in company_targets),
        "discount_mtd": sum(t["mtd"]["discount"] for t in company_targets),
        "incentive_mtd": sum(t["incentive_amount_mtd"] for t in company_targets),
        "incentive_full": sum(t["incentive_amount_full"] for t in company_targets),
        "employee_count": len(company_targets),
    }
    summary["qty_pct_mtd"] = pct(summary["qty_actual_mtd"], summary["qty_prorated"])
    summary["value_pct_mtd"] = pct(summary["expected_mtd"], summary["value_prorated"])
    summary["discount_pct_mtd"] = pct(summary["discount_mtd"], summary["discount_cap"])

    # Day-wise trend (for the selected employee if one is picked, else all
    # employees with a whole-employee target combined) across all days up
    # to days_elapsed, cumulative actual qty sold vs cumulative pro-rated
    # target qty - the core "are we on pace" chart, rendered as a simple
    # proportional bar per day (no charting library in this app).
    daywise = []
    if days_elapsed and company_targets:
        emp_ids = [selected_employee_id] if selected_employee_id else [t["EmployeeID"] for t in company_targets]
        daily_qty_target = sum(t["QtySoldTarget"] for t in company_targets) / days_in_month if days_in_month else 0
        day_rows = db.query(f"""SELECT si.IssueDate AS d, COALESCE(SUM(sil.QtySold), 0) AS qty
                             FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                             WHERE si.EmployeeID IN ({','.join('?' * len(emp_ids))})
                               AND si.IssueDate BETWEEN ? AND ?
                             GROUP BY si.IssueDate""",
                             tuple(emp_ids) + (start.isoformat(), (start + timedelta(days=days_elapsed - 1)).isoformat()))
        qty_by_day = {r["d"]: r["qty"] for r in day_rows}
        for r in unlinked_sale_rows(emp_ids, start.isoformat(), (start + timedelta(days=days_elapsed - 1)).isoformat()):
            qty_by_day[r["date"]] = qty_by_day.get(r["date"], 0) + r["qty"]
        cum_actual = 0.0
        for i in range(days_elapsed):
            d = start + timedelta(days=i)
            cum_actual += qty_by_day.get(d.isoformat(), 0)
            cum_target = round(daily_qty_target * (i + 1), 2)
            daywise.append({"date": d.isoformat(), "qty": round(qty_by_day.get(d.isoformat(), 0), 2),
                             "cum_actual": round(cum_actual, 2), "cum_target": cum_target,
                             "pct": pct(cum_actual, cum_target)})

    # Week-to-date: the current ISO week (Mon-today), clipped to the month
    # and only meaningful when the selected month is the current month.
    wtd = None
    if is_current_month and company_targets:
        emp_ids = [selected_employee_id] if selected_employee_id else [t["EmployeeID"] for t in company_targets]
        week_start = max(today - timedelta(days=today.weekday()), start)
        days_in_week_so_far = (today - week_start).days + 1
        daily_qty_target = sum(t["QtySoldTarget"] for t in company_targets) / days_in_month if days_in_month else 0
        wtd_target_qty = round(daily_qty_target * days_in_week_so_far, 2)
        wtd_rows = db.query(f"""SELECT COALESCE(SUM(sil.QtySold), 0) qty, COALESCE(SUM(sil.DiscountAmount), 0) discount
                             FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID = sil.IssueID
                             WHERE si.EmployeeID IN ({','.join('?' * len(emp_ids))})
                               AND si.IssueDate BETWEEN ? AND ?""",
                             tuple(emp_ids) + (week_start.isoformat(), today.isoformat()), one=True)
        extra = unlinked_sale_rows(emp_ids, week_start.isoformat(), today.isoformat())
        wtd_qty = wtd_rows["qty"] + sum(r["qty"] for r in extra)
        wtd = {"week_start": week_start.isoformat(), "qty_actual": round(wtd_qty, 2),
               "qty_target": wtd_target_qty, "discount": round(wtd_rows["discount"] + sum(r["discount"] for r in extra), 2),
               "pct": pct(wtd_qty, wtd_target_qty)}

    edit_target = None
    edit_target_buckets = []
    edit_id = request.args.get("edit", type=int)
    if edit_id:
        edit_target = db.query("SELECT * FROM Targets WHERE TargetID=?", (edit_id,), one=True)
        if edit_target:
            edit_target_buckets = get_target_buckets(edit_id)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("targets.html", year=year, month=month, month_name=MONTH_NAMES[month],
                            prev_month=prev_month, prev_year=prev_year, next_month=next_month, next_year=next_year,
                            employees=employees, products=products, selected_employee_id=selected_employee_id,
                            targets=targets, summary=summary, daywise=daywise, wtd=wtd,
                            days_in_month=days_in_month, days_elapsed=days_elapsed, is_current_month=is_current_month,
                            is_admin=is_admin, edit_target=edit_target, edit_target_buckets=edit_target_buckets,
                            today=today_str())


@bp.route("/targets/save", methods=["POST"])
def target_save():
    current = get_current_user()
    if not current or current["Role"] != "Admin":
        flash("Only an Admin can set targets.", "error")
        return redirect(url_for("targets_view"))
    f = request.form
    target_id = f.get("target_id", type=int)
    employee_id = f.get("employee_id", type=int)
    product_id = f.get("product_id", type=int) or None
    year = f.get("year", type=int)
    month = f.get("month", type=int)
    if not employee_id or not year or not month:
        flash("Employee, year and month are required.", "error")
        return redirect(url_for("targets_view", year=year or today_str()[:4], month=month or 1))

    qty_target = float(f.get("qty_target") or 0)
    value_target = float(f.get("value_target") or 0)
    discount_cap = float(f.get("discount_cap") or 0)
    discount_rate = float(f.get("discount_rate") or 0)
    base_incentive = float(f.get("base_incentive") or 0)
    notes = (f.get("notes") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")

    # Achievement %% / Multiplier bucket rows - parallel arrays from the dynamic bucket-row
    # form fields; blank trailing rows (from "Add Bucket" then not filling it in) are skipped.
    bucket_pcts = request.form.getlist("bucket_pct[]")
    bucket_mults = request.form.getlist("bucket_mult[]")
    buckets = [(float(p), float(m)) for p, m in zip(bucket_pcts, bucket_mults) if p != "" and m != ""]

    existing = db.query("""SELECT TargetID FROM Targets WHERE EmployeeID=? AND ProductID IS ?
                         AND TargetYear=? AND TargetMonth=?""", (employee_id, product_id, year, month), one=True)
    if existing and (not target_id or existing["TargetID"] == target_id):
        target_id = existing["TargetID"]
        db.execute("""UPDATE Targets SET QtySoldTarget=?, SalesValueTarget=?, DiscountCapAmount=?,
                    DiscountCapRatePerUnit=?, BaseIncentiveAmount=?, Notes=?, UpdatedAt=? WHERE TargetID=?""",
                   (qty_target, value_target, discount_cap, discount_rate, base_incentive, notes, now, target_id))
        flash("Target updated.", "success")
    elif target_id:
        db.execute("""UPDATE Targets SET EmployeeID=?, ProductID=?, TargetYear=?, TargetMonth=?, QtySoldTarget=?,
                    SalesValueTarget=?, DiscountCapAmount=?, DiscountCapRatePerUnit=?, BaseIncentiveAmount=?,
                    Notes=?, UpdatedAt=? WHERE TargetID=?""",
                   (employee_id, product_id, year, month, qty_target, value_target, discount_cap, discount_rate,
                    base_incentive, notes, now, target_id))
        flash("Target updated.", "success")
    else:
        target_id = db.execute("""INSERT INTO Targets (EmployeeID, ProductID, TargetYear, TargetMonth, QtySoldTarget,
                    SalesValueTarget, DiscountCapAmount, DiscountCapRatePerUnit, BaseIncentiveAmount, Notes, UpdatedAt)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (employee_id, product_id, year, month, qty_target, value_target, discount_cap, discount_rate,
                    base_incentive, notes, now))
        flash("Target created.", "success")

    # Buckets are fully replaced on every save - simplest way to keep the editable table in
    # the form (add/remove rows freely) in sync with what's stored, without diffing rows.
    db.execute("DELETE FROM TargetIncentiveBuckets WHERE TargetID=?", (target_id,))
    for achievement_pct, multiplier in buckets:
        db.execute("""INSERT INTO TargetIncentiveBuckets (TargetID, AchievementPct, Multiplier)
                    VALUES (?,?,?)""", (target_id, achievement_pct, multiplier))

    return redirect(url_for("targets_view", year=year, month=month))


@bp.route("/targets/<int:target_id>/delete", methods=["POST"])
def target_delete(target_id):
    current = get_current_user()
    if not current or current["Role"] != "Admin":
        flash("Only an Admin can delete targets.", "error")
        return redirect(url_for("targets_view"))
    t = db.query("SELECT * FROM Targets WHERE TargetID=?", (target_id,), one=True)
    if not t:
        flash("Target not found.", "error")
        return redirect(url_for("targets_view"))
    db.execute("DELETE FROM Targets WHERE TargetID=?", (target_id,))
    flash("Target deleted.", "success")
    return redirect(url_for("targets_view", year=t["TargetYear"], month=t["TargetMonth"]))
