"""customers routes."""
import bulk_import
import db
import os
from core.auth import admin_required, get_current_user
from core.helpers import INDIAN_STATES, STATE_NAME_BY_CODE, _pdf_data_table, format_date_dmy, get_company_settings, get_custom_attachments, get_custom_field_defs, get_custom_values, get_customer_due_info, get_effective_columns, next_customer_account_number, save_custom_fields, today_str
from flask import flash, jsonify, redirect, render_template, request, url_for
from formatting import indian_number_format



from flask import Blueprint

bp = Blueprint("customers", __name__)


# ---------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------


CUSTOMERS_LIST_PAGE_SIZE = 200


@bp.route("/customers")
def customers_list():
    # Same fix as sales_list()/purchases_list(): the search box here used to
    # be client-side-only JS filtering every row already in the DOM, which
    # only ever worked because every Customer was already fetched onto the
    # page - once paginated, that would silently only search the current
    # page. Now a real SQL search, with real pagination.
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = "(cu.CustomerName LIKE ? OR cu.ContactPerson LIKE ? OR cu.Phone LIKE ? OR cu.Zone LIKE ? OR cu.AccountNumber LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like, like, like]

    total = db.query(f"SELECT COUNT(*) c FROM Customers cu WHERE {where}", params, one=True)["c"]
    total_pages = max((total + CUSTOMERS_LIST_PAGE_SIZE - 1) // CUSTOMERS_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * CUSTOMERS_LIST_PAGE_SIZE

    # ParentCustomerName/ParentAccountNumber - the group this row belongs to, when it's a
    # sub-account; SubAccountCount - how many sub-accounts are grouped under this row, when
    # it's itself a parent (0 for an ordinary standalone account).
    customers = db.query(f"""SELECT cu.*, p.CustomerName AS ParentCustomerName, p.AccountNumber AS ParentAccountNumber,
                          (SELECT COUNT(*) FROM Customers ch WHERE ch.ParentCustomerID = cu.CustomerID) AS SubAccountCount
                          FROM Customers cu LEFT JOIN Customers p ON p.CustomerID = cu.ParentCustomerID
                          WHERE {where} ORDER BY cu.CustomerName
                          LIMIT ? OFFSET ?""", params + [CUSTOMERS_LIST_PAGE_SIZE, offset])
    return render_template("customers_list.html", customers=customers, columns=get_effective_columns("Customer"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=CUSTOMERS_LIST_PAGE_SIZE)


@bp.route("/customers/search")
def customers_search():
    """JSON typeahead used by the New/Edit Sale customer search box: returns up to 15
    active customers whose name/phone/zone contains the query (case-insensitive), so a
    salesperson can find and reuse an existing customer instead of retyping their name and
    accidentally creating a duplicate record."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    like = f"%{q}%"
    rows = db.query("""SELECT CustomerID, CustomerName, Address, Phone, Zone, StateCode FROM Customers
                     WHERE IsUnassignedBucket=0 AND Active=1
                       AND (CustomerName LIKE ? OR Phone LIKE ? OR Zone LIKE ?)
                     ORDER BY CustomerName LIMIT 15""", (like, like, like))
    return jsonify([dict(r) for r in rows])


@bp.route("/customers/<int:customer_id>/due")
def customer_due_info(customer_id):
    """JSON lookup used by the New/Edit Sale form: once a specific customer is
    selected (not on every typeahead keystroke), fetch their current
    outstanding due so the form can show the red "customer has dues"
    warning. See get_customer_due_info() for the exact definition."""
    customer = db.query("SELECT CustomerID FROM Customers WHERE CustomerID=?", (customer_id,), one=True)
    if not customer:
        return jsonify({"due": 0, "oldest_due_date": None, "days_overdue": 0}), 404
    company = get_company_settings()
    return jsonify({**get_customer_due_info(customer_id),
                    "mode": company["CreditControlMode"] or "Informational",
                    "block_days": company["CreditBlockDays"] or 0})


@bp.route("/customers/merge-duplicates", methods=["GET", "POST"])
@admin_required
def customers_merge_duplicates():
    """Finds groups of Customers whose name matches case/whitespace-insensitively (the
    exact kind of accidental duplicate resolve_sale_customer() used to create before it
    started reusing an existing name match - e.g. two "Sai phani kirana" rows) and lets an
    Admin merge each group into one: every Sale on the duplicate(s) is re-pointed to the
    chosen primary customer, and the duplicate row(s) are deleted."""
    if request.method == "POST":
        primary_id = int(request.form["primary_id"])
        dup_ids = [int(x) for x in request.form.getlist("dup_id[]") if int(x) != primary_id]
        if dup_ids:
            placeholders = ",".join("?" * len(dup_ids))
            db.execute(f"UPDATE Sales SET CustomerID=? WHERE CustomerID IN ({placeholders})",
                       (primary_id, *dup_ids))
            db.execute(f"DELETE FROM Customers WHERE CustomerID IN ({placeholders})", tuple(dup_ids))
            flash(f"Merged {len(dup_ids)} duplicate(s) into '{db.query('SELECT CustomerName FROM Customers WHERE CustomerID=?', (primary_id,), one=True)['CustomerName']}'.", "success")
        else:
            flash("Nothing to merge — pick at least one duplicate to fold into the primary.", "error")
        return redirect(url_for("customers_merge_duplicates"))

    all_customers = db.query("""SELECT CustomerID, CustomerName, Address, Phone, Zone, Active,
                              (SELECT COUNT(*) FROM Sales WHERE Sales.CustomerID = Customers.CustomerID) AS SaleCount
                              FROM Customers WHERE IsUnassignedBucket=0 ORDER BY CustomerName, CustomerID""")
    groups = {}
    for c in all_customers:
        key = " ".join(c["CustomerName"].split()).lower()
        groups.setdefault(key, []).append(dict(c))
    duplicate_groups = [g for g in groups.values() if len(g) > 1]
    duplicate_groups.sort(key=lambda g: g[0]["CustomerName"].lower())
    return render_template("customers_merge_duplicates.html", duplicate_groups=duplicate_groups)


def customer_parent_candidates(exclude_cid=None):
    """Customers eligible to be picked as a Parent Account on the Customer
    form - root accounts only (no parent of their own, so the hierarchy
    never nests past 2 levels), excluding the system Unassigned bucket and
    the customer being edited itself."""
    rows = db.query("""SELECT CustomerID, CustomerName, AccountNumber FROM Customers
                     WHERE IsUnassignedBucket=0 AND ParentCustomerID IS NULL
                     ORDER BY CustomerName""")
    if exclude_cid:
        rows = [r for r in rows if r["CustomerID"] != exclude_cid]
    return rows


def customer_has_children(cid):
    """True if other customers are already grouped under this one as a
    parent - used to lock the Parent Account field on the form, since
    validate_customer_parent() would reject giving such a customer a
    parent of its own (flat, 2-level hierarchy only)."""
    if not cid:
        return False
    return bool(db.query("SELECT 1 FROM Customers WHERE ParentCustomerID=? LIMIT 1", (cid,), one=True))


def validate_customer_parent(cid, parent_id_raw):
    """Enforces a flat, 2-level account grouping (see ParentCustomerID in
    db/schema.sql): a root account can have any number of children, but a
    child can't itself be a parent, and a customer that already has
    children can't be given a parent of its own. Returns
    (clean_parent_id_or_None, error_message_or_None)."""
    parent_id_raw = (parent_id_raw or "").strip()
    if not parent_id_raw:
        return None, None
    try:
        parent_id = int(parent_id_raw)
    except ValueError:
        return None, "Invalid parent account."
    if cid and parent_id == cid:
        return None, "A customer can't be its own parent account."
    parent = db.query("SELECT CustomerID, ParentCustomerID, IsUnassignedBucket FROM Customers WHERE CustomerID=?",
                       (parent_id,), one=True)
    if not parent or parent["IsUnassignedBucket"]:
        return None, "Selected parent account doesn't exist."
    if parent["ParentCustomerID"]:
        return None, "That account is itself a sub-account, so it can't be used as a parent - only a root account can be a parent."
    if cid:
        has_children = db.query("SELECT 1 FROM Customers WHERE ParentCustomerID=? LIMIT 1", (cid,), one=True)
        if has_children:
            return None, "This account already has its own sub-accounts, so it can't be assigned a parent too."
    return parent_id, None


@bp.route("/customers/new", methods=["GET", "POST"])
@bp.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
def customer_form(cid=None):
    customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (cid,), one=True) if cid else None
    if request.method == "POST":
        f = request.form
        if not f.get("zone", "").strip():
            flash("Zone is required.", "error")
            return render_template("customer_form.html", customer=customer, states=INDIAN_STATES,
                                    custom_fields=get_custom_field_defs("Customer"),
                                    custom_values=get_custom_values("Customer", cid) if cid else {},
                                    cf_record_id=cid, custom_attachments=get_custom_attachments("Customer", cid),
                                    zones=db.query("SELECT * FROM Zones WHERE Active=1 ORDER BY DisplayOrder, ZoneID"),
                                    parent_candidates=customer_parent_candidates(exclude_cid=cid), has_children=customer_has_children(cid))
        parent_id, parent_error = validate_customer_parent(cid, f.get("parent_customer_id"))
        if parent_error:
            flash(parent_error, "error")
            return render_template("customer_form.html", customer=customer, states=INDIAN_STATES,
                                    custom_fields=get_custom_field_defs("Customer"),
                                    custom_values=get_custom_values("Customer", cid) if cid else {},
                                    cf_record_id=cid, custom_attachments=get_custom_attachments("Customer", cid),
                                    zones=db.query("SELECT * FROM Zones WHERE Active=1 ORDER BY DisplayOrder, ZoneID"),
                                    parent_candidates=customer_parent_candidates(exclude_cid=cid), has_children=customer_has_children(cid))
        state_code = f.get("state_code", "")
        state_name = STATE_NAME_BY_CODE.get(state_code, "")
        try:
            latitude = float(f.get("latitude")) if (f.get("latitude") or "").strip() else None
            longitude = float(f.get("longitude")) if (f.get("longitude") or "").strip() else None
        except ValueError:
            latitude = longitude = None
        args = (f["customer_name"], f["contact_person"], f["phone"], f["email"], f["address"], f.get("zone", ""),
                f["gstin"], state_name, state_code,
                float(f["credit_limit"] or 0), int(f["credit_days"] or 0), 1 if f.get("active") else 0, parent_id,
                latitude, longitude)
        if cid:
            db.execute("""UPDATE Customers SET CustomerName=?, ContactPerson=?, Phone=?, Email=?, Address=?, Zone=?,
                        GSTIN=?, State=?, StateCode=?, CreditLimit=?, CreditDays=?, Active=?, ParentCustomerID=?,
                        Latitude=?, Longitude=? WHERE CustomerID=?""",
                       args + (cid,))
            save_custom_fields("Customer", cid, f)
        else:
            new_id = db.execute("""INSERT INTO Customers (CustomerName, ContactPerson, Phone, Email, Address, Zone,
                        GSTIN, State, StateCode, CreditLimit, CreditDays, Active, ParentCustomerID, Latitude,
                        Longitude, AccountNumber)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", args + (next_customer_account_number(),))
            save_custom_fields("Customer", new_id, f)
        flash("Customer saved.", "success")
        return redirect(url_for("customers_list"))
    custom_fields = get_custom_field_defs("Customer")
    custom_values = get_custom_values("Customer", cid) if cid else {}
    zones = db.query("SELECT * FROM Zones WHERE Active=1 ORDER BY DisplayOrder, ZoneID")
    # A customer whose already-saved Zone doesn't match any (active) Zones entry -
    # e.g. it was hidden/deleted from the list, or predates the Zones table on an
    # install where the seed migration hasn't been run yet - still needs to show up
    # as a selectable option so the dropdown doesn't silently drop their current value.
    if customer and customer["Zone"] and customer["Zone"] not in {z["ZoneName"] for z in zones}:
        zones = list(zones) + [{"ZoneID": None, "ZoneName": customer["Zone"]}]
    return render_template("customer_form.html", customer=customer, states=INDIAN_STATES,
                            parent_candidates=customer_parent_candidates(exclude_cid=cid), has_children=customer_has_children(cid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=cid, custom_attachments=get_custom_attachments("Customer", cid),
                            zones=zones)


def _customer_group_ids(cid, sub_accounts):
    """This customer's own CustomerID plus every sub-account grouped under
    it (empty if it has none) - the set of accounts a consolidated group
    ledger spans. Only meaningful for a parent/root account; a child's own
    sub_accounts is always empty (a flat 2-level hierarchy - see
    validate_customer_parent())."""
    return [cid] + [s["CustomerID"] for s in sub_accounts]


def _ledger_sales_for_group(customer_ids):
    """Like the plain per-customer Sales query in customer_purchase_history()/
    customer_ledger_pdf(), but across every account in a group and tagged
    with CustomerName - _build_customer_ledger() prefixes each entry with
    it so a consolidated statement still shows which account each line
    belongs to."""
    placeholders = ",".join("?" * len(customer_ids))
    return db.query(f"""SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.Status, s.PaymentStatus,
                      s.TaxableAmount, s.TotalAmount, s.AmountReceived,
                      (s.TaxableAmount - s.AmountReceived) AS Due, c.CustomerName
                      FROM Sales s JOIN Customers c ON c.CustomerID = s.CustomerID
                      WHERE s.CustomerID IN ({placeholders}) AND s.Status<>'Cancelled'
                      ORDER BY s.SaleDate, s.SaleID""", tuple(customer_ids))


def _build_customer_ledger(sales_asc, date_from, date_to):
    """Chronological statement-of-account for a customer: every invoice (debit,
    at Taxable Value - matching how "Due" is computed everywhere else in the
    app on this customer's history/Accounts Receivable, i.e. GST isn't chased
    as part of what's owed) and every payment received against it (credit),
    with a running balance. Payments come from SalePayments; for a sale that
    has AmountReceived on it but no matching SalePayments rows (e.g. entered
    as a lump sum on the Sale form before per-payment recording existed, or
    auto-set via Stock Issue reconciliation), the uncovered leftover is shown
    as a single "Payment received" credit dated the sale date, so the
    ledger's closing balance always reconciles with the Due shown on this
    customer's invoices without requiring any backfill.

    Also doubles as the consolidated GROUP ledger builder: when a caller
    passes rows from _ledger_sales_for_group() (spanning several accounts,
    each row carrying a CustomerName) instead of a single customer's own
    Sales, every entry's detail text is prefixed with that account's name
    so the combined running balance still shows which account each
    transaction belongs to. A plain single-account caller's rows don't
    carry CustomerName, so this is a no-op for every existing use."""
    sale_ids = [s["SaleID"] for s in sales_asc]
    payments_by_sale = {}
    if sale_ids:
        placeholders = ",".join("?" * len(sale_ids))
        pay_rows = db.query(f"""SELECT * FROM SalePayments WHERE SaleID IN ({placeholders})
                              ORDER BY PaymentDate, PaymentID""", tuple(sale_ids))
        for p in pay_rows:
            payments_by_sale.setdefault(p["SaleID"], []).append(p)

    entries = []
    for s in sales_asc:
        account_name = s["CustomerName"] if "CustomerName" in s.keys() else None
        prefix = f"{account_name} — " if account_name else ""
        entries.append({
            "date": s["SaleDate"], "type": "Invoice", "ref": s["InvoiceNumber"], "account": account_name,
            "detail": f"{prefix}Invoice {s['InvoiceNumber']}",
            "debit": round(s["TaxableAmount"] or 0, 2), "credit": 0, "sort2": 0,
        })
        covered = 0.0
        for p in payments_by_sale.get(s["SaleID"], []):
            method_bit = f" ({p['PaymentMethod']})" if p["PaymentMethod"] else ""
            notes_bit = f" — {p['Notes']}" if p["Notes"] else ""
            entries.append({
                "date": p["PaymentDate"], "type": "Payment", "ref": s["InvoiceNumber"], "account": account_name,
                "detail": f"{prefix}Payment received — Invoice {s['InvoiceNumber']}{method_bit}{notes_bit}",
                "debit": 0, "credit": round(p["Amount"], 2), "sort2": 1,
            })
            covered += p["Amount"]
        leftover = round((s["AmountReceived"] or 0) - covered, 2)
        if leftover > 0.005:
            entries.append({
                "date": s["SaleDate"], "type": "Payment", "ref": s["InvoiceNumber"], "account": account_name,
                "detail": f"{prefix}Payment received — Invoice {s['InvoiceNumber']}",
                "debit": 0, "credit": leftover, "sort2": 1,
            })

    entries.sort(key=lambda e: (e["date"], e["sort2"]))

    opening_balance = 0.0
    running = 0.0
    displayed = []
    for e in entries:
        running = round(running + e["debit"] - e["credit"], 2)
        if date_from and e["date"] < date_from:
            opening_balance = running
            continue
        if date_to and e["date"] > date_to:
            continue
        row = dict(e)
        row["balance"] = running
        displayed.append(row)

    closing_balance = round(displayed[-1]["balance"], 2) if displayed else round(opening_balance, 2)
    return {
        "entries": displayed,
        "opening_balance": round(opening_balance, 2),
        "closing_balance": closing_balance,
        "total_debit": round(sum(e["debit"] for e in displayed), 2),
        "total_credit": round(sum(e["credit"] for e in displayed), 2),
    }


@bp.route("/customers/<int:cid>/ledger/pdf")
def customer_ledger_pdf(cid):
    """PDF of the same Ledger Statement shown on the history page (same ?from=&to=&group=1)."""
    customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (cid,), one=True)
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers_list"))
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    sub_accounts = db.query("""SELECT CustomerID, CustomerName FROM Customers
                             WHERE ParentCustomerID=? ORDER BY CustomerName""", (cid,))
    group_mode = request.args.get("group") == "1" and bool(sub_accounts)
    if group_mode:
        sales_asc = _ledger_sales_for_group(_customer_group_ids(cid, sub_accounts))
        title = f"Consolidated Group Ledger Statement — {customer['CustomerName']} + {len(sub_accounts)} sub-account(s)"
    else:
        sales_asc = db.query("""SELECT SaleID, InvoiceNumber, SaleDate, TaxableAmount, AmountReceived
                          FROM Sales WHERE CustomerID=? AND Status<>'Cancelled'
                          ORDER BY SaleDate, SaleID""", (cid,))
        title = f"Ledger Statement — {customer['CustomerName']}"
    ledger = _build_customer_ledger(sales_asc, date_from, date_to)

    def bal(v):
        return f"{indian_number_format(abs(v))} {'Dr' if v > 0.005 else 'Cr' if v < -0.005 else ''}".strip()

    def row(d, det, ref, dr, cr, b):
        return {"Date": d, "Transaction Details": det, "Voucher / Ref No.": ref,
                "Debit (Dr)": dr, "Credit (Cr)": cr, "Running Balance": b}

    rows = []
    if date_from:
        rows.append(row(format_date_dmy(date_from), "Opening Balance", "-", "-", "-", bal(ledger["opening_balance"])))
    for e in ledger["entries"]:
        rows.append(row(format_date_dmy(e["date"]), e["detail"], e["ref"] or "-",
                        indian_number_format(e["debit"]) if e["debit"] else "-",
                        indian_number_format(e["credit"]) if e["credit"] else "-", bal(e["balance"])))
    rows.append(row("Total", "Closing Balance", "-", indian_number_format(ledger["total_debit"]),
                    indian_number_format(ledger["total_credit"]), bal(ledger["closing_balance"])))
    buf = rows_to_pdf(rows, title)
    from flask import send_file
    filename_stub = f"{customer['CustomerName']}_Group" if group_mode else customer['CustomerName']
    return send_file(buf, as_attachment=True,
                      download_name=f"Ledger_{filename_stub}_{today_str()}.pdf".replace(" ", "_"),
                      mimetype="application/pdf")


@bp.route("/customers/<int:cid>/history")
def customer_purchase_history(cid):
    """A single customer's full purchase (Sales) history with this business
    (?view=overview|products|ledger):
      - overview (default): every invoice, its line items, running totals, and top 10 products bought.
      - products: full product-wise purchase breakdown (qty + amount), optionally scoped to a date range.
      - ledger: a running-balance statement of account (invoices as debits, payments as credits).
    An optional ?from=&to= date range scopes all three views to that period - Overview/Products simply
    filter which invoices count; Ledger additionally rolls everything before "from" into an Opening
    Balance line, same as a bank/vendor statement of account."""
    customer = db.query("SELECT * FROM Customers WHERE CustomerID=?", (cid,), one=True)
    if not customer:
        flash("Customer not found.", "error")
        return redirect(url_for("customers_list"))

    parent_account = db.query("SELECT CustomerID, CustomerName, AccountNumber FROM Customers WHERE CustomerID=?",
                               (customer["ParentCustomerID"],), one=True) if customer["ParentCustomerID"] else None
    sub_accounts = db.query("""SELECT CustomerID, CustomerName, AccountNumber FROM Customers
                             WHERE ParentCustomerID=? ORDER BY CustomerName""", (cid,))
    # Consolidated group ledger - only meaningful for a parent account that actually has
    # sub-accounts; a childless account or a sub-account itself just falls back to its own.
    group_mode = request.args.get("group") == "1" and bool(sub_accounts)

    view = request.args.get("view", "overview")
    if view not in ("overview", "products", "ledger"):
        view = "overview"
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    sales_asc = db.query("""SELECT s.SaleID, s.InvoiceNumber, s.SaleDate, s.Status, s.PaymentStatus,
                      s.TaxableAmount, s.TotalAmount, s.AmountReceived,
                      (s.TaxableAmount - s.AmountReceived) AS Due
                      FROM Sales s WHERE s.CustomerID=? AND s.Status<>'Cancelled'
                      ORDER BY s.SaleDate, s.SaleID""", (cid,))

    def in_range(d):
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
        return True

    filtered_sales = [s for s in sales_asc if in_range(s["SaleDate"])] if (date_from or date_to) else list(sales_asc)

    sale_ids = [s["SaleID"] for s in filtered_sales]
    lines_by_sale = {}
    product_totals = {}
    if sale_ids:
        placeholders = ",".join("?" * len(sale_ids))
        lines = db.query(f"""SELECT sl.SaleID, pr.ProductName, sl.Qty, sl.UnitPrice, sl.DiscountAmount, sl.LineTotal
                          FROM SalesLines sl JOIN Products pr ON pr.ProductID=sl.ProductID
                          WHERE sl.SaleID IN ({placeholders}) ORDER BY sl.LineID""", tuple(sale_ids))
        for l in lines:
            lines_by_sale.setdefault(l["SaleID"], []).append(l)
            pt = product_totals.setdefault(l["ProductName"], {"product": l["ProductName"], "qty": 0, "amount": 0})
            pt["qty"] += l["Qty"]
            pt["amount"] += l["LineTotal"]
    product_rows = sorted(product_totals.values(), key=lambda p: -p["amount"])
    for p in product_rows:
        p["amount"] = round(p["amount"], 2)
        p["qty"] = round(p["qty"], 2)
    top_products = product_rows[:10]

    totals = {
        "invoice_count": len(filtered_sales),
        "total_taxable": round(sum(s["TaxableAmount"] for s in filtered_sales), 2),
        "total_invoiced": round(sum(s["TotalAmount"] for s in filtered_sales), 2),
        "total_received": round(sum(s["AmountReceived"] for s in filtered_sales), 2),
        "total_due": round(sum(max(s["Due"], 0) for s in filtered_sales), 2),
        "first_purchase": min((s["SaleDate"] for s in filtered_sales), default=None),
        "last_purchase": max((s["SaleDate"] for s in filtered_sales), default=None),
    }

    ledger = None
    if view == "ledger":
        if group_mode:
            ledger = _build_customer_ledger(
                _ledger_sales_for_group(_customer_group_ids(cid, sub_accounts)), date_from, date_to)
        else:
            ledger = _build_customer_ledger(sales_asc, date_from, date_to)

    sales = sorted(filtered_sales, key=lambda s: (s["SaleDate"], s["SaleID"]), reverse=True)

    extra_qs = ""
    if date_from:
        extra_qs += "&from=" + date_from
    if date_to:
        extra_qs += "&to=" + date_to

    return render_template("customer_purchase_history.html", customer=customer, sales=sales,
                            lines_by_sale=lines_by_sale, top_products=top_products, product_rows=product_rows,
                            totals=totals, view=view, date_from=date_from, date_to=date_to, ledger=ledger,
                            extra_qs=extra_qs, parent_account=parent_account, sub_accounts=sub_accounts,
                            group_mode=group_mode)


# ---------------------------------------------------------------------
# Customers: bulk Excel upload
#
# Off an app-provided template (download it, fill it in, upload it) -
# not a specific vendor's own format. A row whose Customer Name exactly
# matches (case-insensitive) an existing customer UPDATES that customer's
# details from the file; anything else is added as a new customer. This
# is a one-step upload (no separate matching/review screen) since there's
# nothing here that needs a human pick, unlike the Purchases vendor-file
# import which has to match unfamiliar material codes to Products.
# ---------------------------------------------------------------------


@bp.route("/customers/import/template")
def customers_import_template():
    from flask import send_file
    buf = bulk_import.build_customer_template()
    return send_file(buf, as_attachment=True, download_name="Customer_Upload_Template.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/customers/import", methods=["GET", "POST"])
def customers_import_upload():
    if request.method == "POST":
        file_storage = request.files.get("file")
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("customers_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = bulk_import.parse_customers_excel(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("customers_import_upload"))

        created = updated = 0
        for row in result["rows"]:
            state_code = row["state_code"]
            state_name = row["state"]
            if state_code and not state_name:
                state_name = STATE_NAME_BY_CODE.get(state_code, "")
            elif state_name and not state_code:
                for code, name in INDIAN_STATES:
                    if name.lower() == state_name.lower():
                        state_code = code
                        break

            # Match key preference: GSTIN (most specific, unique to a business) first,
            # then Phone, then fall back to Customer Name - matches how a duplicate is
            # most reliably identified in practice for this business.
            existing = None
            if row["gstin"]:
                existing = db.query("SELECT * FROM Customers WHERE GSTIN=? COLLATE NOCASE",
                                    (row["gstin"],), one=True)
            if not existing and row["phone"]:
                existing = db.query("SELECT * FROM Customers WHERE Phone=? AND IsUnassignedBucket=0",
                                    (row["phone"],), one=True)
            if not existing:
                existing = db.query("SELECT * FROM Customers WHERE CustomerName=? COLLATE NOCASE",
                                    (row["customer_name"],), one=True)
            args = (row["customer_name"], row["contact_person"], row["phone"], row["email"], row["address"],
                    row["gstin"], state_name, state_code, row["credit_limit"], row["credit_days"])
            if existing:
                db.execute("""UPDATE Customers SET CustomerName=?, ContactPerson=?, Phone=?, Email=?, Address=?,
                            GSTIN=?, State=?, StateCode=?, CreditLimit=?, CreditDays=? WHERE CustomerID=?""",
                           args + (existing["CustomerID"],))
                updated += 1
            else:
                db.execute("""INSERT INTO Customers (CustomerName, ContactPerson, Phone, Email, Address, GSTIN,
                            State, StateCode, CreditLimit, CreditDays, Active, AccountNumber)
                            VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                           args + (next_customer_account_number(),))
                created += 1

        flash(f"{result['message']} {created} new customer(s) added, {updated} existing customer(s) updated.",
              "success")
        return redirect(url_for("customers_list"))

    return render_template("customers_import_upload.html")


# ---------------------------------------------------------------------
# Excel export - "Export to Excel" on every major list page. Each spec is
# a plain SELECT with friendly column aliases (via `AS`) so the header row
# of the download matches what the column actually means; no filters are
# applied here - it's always the full list, matching what a fresh visit to
# that list page would show unfiltered.
# ---------------------------------------------------------------------


EXPORT_SPECS = {
    "products": dict(title="Products", admin_only=False, query="""
        SELECT ProductName AS "Product Name", SKU, Category, Unit, CostPrice AS "Cost Price",
               SellingPrice AS "Selling Price", SchemeName AS "Scheme Name", SchemePercent AS "Scheme %",
               IncentivePerUnit AS "Incentive/Unit",
               MinStock AS "Min Stock", MaxStock AS "Max Stock", HSNCode AS "HSN Code",
               GSTRate AS "GST Rate", CASE Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Products ORDER BY ProductName""", detail_query="SELECT * FROM Products ORDER BY ProductName"),
    "suppliers": dict(title="Suppliers", admin_only=True, query="""
        SELECT SupplierName AS "Supplier Name", ContactPerson AS "Contact Person", Phone, Email, Address, GSTIN,
               CASE Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Suppliers ORDER BY SupplierName""", detail_query="SELECT * FROM Suppliers ORDER BY SupplierName"),
    "customers": dict(title="Customers", admin_only=False, query="""
        SELECT c.AccountNumber AS "Account Number", c.CustomerName AS "Customer Name",
               c.ContactPerson AS "Contact Person", c.Phone, c.Email, c.Address, c.GSTIN,
               c.State, c.StateCode AS "State Code", p.CustomerName AS "Parent Account",
               c.CreditLimit AS "Credit Limit", c.CreditDays AS "Credit Days",
               CASE c.Active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status
        FROM Customers c LEFT JOIN Customers p ON p.CustomerID = c.ParentCustomerID
        WHERE c.IsUnassignedBucket=0 ORDER BY c.CustomerName""",
        detail_query="SELECT * FROM Customers WHERE IsUnassignedBucket=0 ORDER BY CustomerName"),
    "purchases": dict(title="Purchases", admin_only=True, query="""
        SELECT p.PurchaseDate AS "Purchase Date", s.SupplierName AS Supplier, p.PONumber AS "PO Number",
               p.InvoiceNumber AS "Invoice Number", p.Status, p.PaymentStatus AS "Payment Status",
               p.TaxableAmount AS "Taxable Amount", p.TotalAmount AS "Total Amount"
        FROM Purchases p JOIN Suppliers s ON s.SupplierID=p.SupplierID
        ORDER BY p.PurchaseDate DESC, p.PurchaseID DESC""", detail_query="""
        SELECT p.PurchaseDate AS "Purchase Date", s.SupplierName AS Supplier, p.PONumber AS "PO Number",
               p.InvoiceNumber AS "Invoice Number", p.Status, p.PaymentStatus AS "Payment Status",
               pr.ProductName AS Product, pl.Qty, pl.UnitCost AS "Unit Cost", pl.HSNCode AS "HSN Code",
               pl.GSTRate AS "GST Rate %", pl.TaxableValue AS "Taxable Value",
               (pl.CGSTAmount + pl.SGSTAmount + pl.IGSTAmount) AS "Tax Amount", pl.LineTotal AS "Line Total"
        FROM PurchaseLines pl JOIN Purchases p ON p.PurchaseID=pl.PurchaseID
             JOIN Suppliers s ON s.SupplierID=p.SupplierID JOIN Products pr ON pr.ProductID=pl.ProductID
        ORDER BY p.PurchaseDate DESC, p.PurchaseID DESC, pl.LineID""", detail_is_line_items=True),
    "sales": dict(title="Sales", admin_only=False, query="""
        SELECT s.SaleDate AS "Sale Date", c.CustomerName AS Customer, s.InvoiceNumber AS "Invoice Number",
               s.Status, s.PaymentStatus AS "Payment Status", s.TaxableAmount AS "Taxable Amount",
               s.TotalAmount AS "Total Amount", s.AmountReceived AS "Amount Received"
        FROM Sales s JOIN Customers c ON c.CustomerID=s.CustomerID
        ORDER BY s.SaleDate DESC, s.SaleID DESC""", detail_query="""
        SELECT s.SaleDate AS "Sale Date", c.CustomerName AS Customer, s.InvoiceNumber AS "Invoice Number",
               s.Status, s.PaymentStatus AS "Payment Status", e.EmployeeName AS Salesperson,
               pr.ProductName AS "Product Sold", sl.Qty, sl.UnitPrice AS "Unit Price",
               sl.DiscountAmount AS "Discount ₹", sl.HSNCode AS "HSN Code", sl.GSTRate AS "GST Rate %",
               sl.TaxableValue AS "Taxable Value", (sl.CGSTAmount + sl.SGSTAmount + sl.IGSTAmount) AS "Tax Amount",
               sl.LineTotal AS "Line Total", s.AmountReceived AS "Amount Received (whole invoice)",
               c.ContactPerson AS "Customer Contact Person", c.Phone AS "Customer Phone",
               c.Email AS "Customer Email", c.Address AS "Customer Address", c.Zone AS "Customer Zone",
               c.GSTIN AS "Customer GSTIN", c.State AS "Customer State", c.StateCode AS "Customer State Code",
               c.CreditLimit AS "Customer Credit Limit", c.CreditDays AS "Customer Credit Days"
        FROM SalesLines sl JOIN Sales s ON s.SaleID=sl.SaleID JOIN Customers c ON c.CustomerID=s.CustomerID
             JOIN Products pr ON pr.ProductID=sl.ProductID LEFT JOIN Employees e ON e.EmployeeID=s.EmployeeID
        ORDER BY s.SaleDate DESC, s.SaleID DESC, sl.LineID""", detail_is_line_items=True),
    "stock_issues": dict(title="Stock Issues", admin_only=False, query="""
        SELECT si.IssueDate AS "Issue Date", e.EmployeeName AS Salesperson, si.Status,
               si.ExpectedAmount AS "Expected Amount", si.CashCollected AS "Cash Collected",
               si.SchemeAmount AS "Scheme Amount", si.AmountDue AS "Amount Due",
               si.PaymentStatus AS "Payment Status", si.ClaimStatus AS "Claim Status"
        FROM StockIssues si JOIN Employees e ON e.EmployeeID=si.EmployeeID
        ORDER BY si.IssueDate DESC, si.IssueID DESC""", detail_query="""
        SELECT si.IssueDate AS "Issue Date", e.EmployeeName AS Salesperson, si.Status,
               pr.ProductName AS Product, sil.QtyIssued AS "Qty Issued", sil.UnitPrice AS "Unit Price",
               sil.QtySold AS "Qty Sold", sil.QtyReturned AS "Qty Returned", sil.QtyFree AS "Qty Free",
               sil.DiscountAmount AS "Discount ₹", sil.SchemeClaimAmount AS "Scheme Claim ₹",
               sil.LineComments AS "Comments"
        FROM StockIssueLines sil JOIN StockIssues si ON si.IssueID=sil.IssueID
             JOIN Employees e ON e.EmployeeID=si.EmployeeID JOIN Products pr ON pr.ProductID=sil.ProductID
        ORDER BY si.IssueDate DESC, si.IssueID DESC, sil.LineID""", detail_is_line_items=True),
    "expenses": dict(title="Expenses", admin_only=False, query="""
        SELECT e.ExpenseDate AS "Expense Date", e.Category, v.RegistrationNumber AS Vehicle, e.PaidTo AS "Paid To",
               e.PaymentMode AS "Payment Mode", e.Amount, e.Description
        FROM Expenses e LEFT JOIN Vehicles v ON v.VehicleID=e.VehicleID
        ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC""", detail_query="""
        SELECT e.* FROM Expenses e ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC"""),
    "vehicles": dict(title="Vehicles", admin_only=False, query="""
        SELECT RegistrationNumber AS "Registration Number", VehicleType AS "Vehicle Type", Make, Model,
               CurrentOdometer AS "Current Odometer", InsuranceExpiry AS "Insurance Expiry",
               PermitExpiry AS "Permit Expiry", PUCExpiry AS "PUC Expiry", FitnessExpiry AS "Fitness Expiry", Status
        FROM Vehicles ORDER BY RegistrationNumber""", detail_query="SELECT * FROM Vehicles ORDER BY RegistrationNumber"),
    "maintenance": dict(title="Maintenance", admin_only=False, query="""
        SELECT v.RegistrationNumber AS Vehicle, vm.ServiceType AS "Service Type", vm.ServiceDate AS "Service Date",
               vm.Odometer, vm.NextDueDate AS "Next Due Date", vm.Cost, vm.ServiceCenter AS "Service Center", vm.Status
        FROM VehicleMaintenance vm JOIN Vehicles v ON v.VehicleID=vm.VehicleID
        ORDER BY vm.ServiceDate DESC, vm.MaintenanceID DESC""", detail_query="""
        SELECT v.RegistrationNumber AS Vehicle, vm.* FROM VehicleMaintenance vm JOIN Vehicles v ON v.VehicleID=vm.VehicleID
        ORDER BY vm.ServiceDate DESC, vm.MaintenanceID DESC"""),
    "salary": dict(title="Salary Schedule", admin_only=True, query="""
        SELECT e.EmployeeName AS Employee, sp.SalaryYear AS Year, sp.SalaryMonth AS Month,
               sp.GrossSalary AS "Gross Salary", sp.LOPDays AS "LOP Days", sp.BasicAmount AS Basic,
               sp.AdvanceDeducted AS "Advance Deducted", sp.NetPayable AS "Net Payable", sp.Status,
               sp.PaymentDate AS "Payment Date"
        FROM SalaryPayments sp JOIN Employees e ON e.EmployeeID=sp.EmployeeID
        ORDER BY sp.SalaryYear DESC, sp.SalaryMonth DESC, e.EmployeeName""", detail_query="""
        SELECT e.EmployeeName AS Employee, sp.* FROM SalaryPayments sp JOIN Employees e ON e.EmployeeID=sp.EmployeeID
        ORDER BY sp.SalaryYear DESC, sp.SalaryMonth DESC, e.EmployeeName"""),
    "employees": dict(title="Employees", admin_only=True, query="""
        SELECT EmployeeName AS "Employee Name", Designation, Phone, JoinDate AS "Join Date",
               MonthlySalary AS "Monthly Salary", Status
        FROM Employees ORDER BY EmployeeName""", detail_query="SELECT * FROM Employees ORDER BY EmployeeName"),
    "advances": dict(title="Advance Payments", admin_only=True, query="""
        SELECT a.AdvanceDate AS "Advance Date", e.EmployeeName AS Employee, a.Amount, a.Reason,
               a.RepaymentMonths AS "Repayment Months", a.MonthlyDeduction AS "Monthly Deduction",
               a.BalanceRemaining AS "Balance Remaining", a.Status
        FROM AdvancePayments a JOIN Employees e ON e.EmployeeID=a.EmployeeID
        ORDER BY a.AdvanceDate DESC""", detail_query="""
        SELECT e.EmployeeName AS Employee, a.* FROM AdvancePayments a JOIN Employees e ON e.EmployeeID=a.EmployeeID
        ORDER BY a.AdvanceDate DESC"""),
    "scheme_claims": dict(title="Scheme Claims", admin_only=True, query="""
        SELECT SchemeName AS "Scheme Name", ClaimDate AS "Claim Date", ApplicableProducts AS "Applicable Products",
               Description, ClaimAmount AS "Claim Amount", Status, ClaimedAt AS "Claimed At",
               ReceivedAt AS "Received At", ReceivedAmount AS "Received Amount", Notes
        FROM SchemeClaims ORDER BY ClaimDate DESC, ClaimID DESC""",
        detail_query="SELECT * FROM SchemeClaims ORDER BY ClaimDate DESC, ClaimID DESC"),
}


def _write_xlsx_sheet(ws, rows):
    import openpyxl
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for r in rows:
            ws.append([r[h] for h in headers])
        for i in range(1, len(headers) + 1):
            letter = openpyxl.utils.get_column_letter(i)
            ws.column_dimensions[letter].width = 18
        # AutoFilter + a frozen header row give every exported sheet Excel's
        # native column search/sort/filter dropdowns "for free" - e.g. this is
        # what lets you search/filter the Customer Name column on the
        # Customers export without any extra UI on our end.
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
    else:
        ws.append(["No data for this report"])


def rows_to_xlsx_multi(sheets, active_index=0):
    """Like rows_to_xlsx(), but writes multiple (title, rows) pairs as
    separate worksheets in one workbook - used so every "Export to Excel"
    button can offer a Summary tab (matches the on-screen list/columns) plus
    a Detail tab (every underlying field, including line items for
    header+lines records like Sales/Purchases/Stock Issues) in one file.

    active_index picks which sheet Excel opens showing - the line-item Detail
    tab for a header+lines export (see EXPORT_SPECS' detail_is_line_items),
    so a user drilling into Purchases/Sales/Stock Issues sees the actual line
    items first rather than having to notice and click a second tab; Summary
    stays the default everywhere else, where Detail is just the same rows
    with every raw column rather than a deeper breakdown."""
    import openpyxl
    import io
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title=(title or "Sheet")[:31])
        _write_xlsx_sheet(ws, rows)
    wb.active = active_index
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def rows_to_pdf(rows, title="Report"):
    """Same full-table export as rows_to_xlsx(), formatted as a printable
    PDF (reportlab, already used by invoice_pdf.py - no new dependency).
    Landscape A4 since most of these tables are wide."""
    import io
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("t", parent=styles["Normal"], fontSize=14, alignment=TA_CENTER, fontName="Helvetica-Bold")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=12 * mm, bottomMargin=12 * mm,
                             leftMargin=10 * mm, rightMargin=10 * mm)
    story = [Paragraph(title, title_style), Spacer(1, 6),
              Paragraph(f"Generated {today_str()}", styles["Normal"]), Spacer(1, 10),
              _pdf_data_table(rows)]
    doc.build(story)
    buf.seek(0)
    return buf


def _check_export_access(spec):
    """Returns a redirect Response if this export is blocked for the
    current user, else None."""
    if spec["admin_only"]:
        current = get_current_user()
        if not current or current["Role"] != "Admin":
            flash("Only Admin accounts can export this report.", "error")
            return redirect(url_for("dashboard"))
    return None


@bp.route("/export/<module>")
def export_module(module):
    """Two-tab workbook: "Summary" is the same curated columns this export
    has always had (matches what's shown on the module's own list page),
    and "Detail" is the fuller data dump - every underlying field, and for
    header+lines records (Sales, Purchases, Stock Issues) one row per line
    item (product, qty, price, discount, etc.) rather than one row per
    invoice/issue. Both sheets get Excel's native AutoFilter dropdowns on
    the header row, so e.g. the Customers export's "Detail" sheet can be
    searched/filtered by Customer Name right inside Excel."""
    spec = EXPORT_SPECS.get(module)
    if not spec:
        flash("Unknown report.", "error")
        return redirect(url_for("dashboard"))
    blocked = _check_export_access(spec)
    if blocked:
        return blocked
    summary_rows = db.query(spec["query"])
    sheets = [("Summary", summary_rows)]
    detail_query = spec.get("detail_query")
    active_index = 0
    if detail_query:
        sheets.append(("Detail", db.query(detail_query)))
        if spec.get("detail_is_line_items"):
            active_index = 1
    buf = rows_to_xlsx_multi(sheets, active_index=active_index)
    from flask import send_file
    filename = f"{spec['title'].replace(' ', '_')}_{today_str()}.xlsx"
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/export/<module>/pdf")
def export_module_pdf(module):
    spec = EXPORT_SPECS.get(module)
    if not spec:
        flash("Unknown report.", "error")
        return redirect(url_for("dashboard"))
    blocked = _check_export_access(spec)
    if blocked:
        return blocked
    rows = db.query(spec["query"])
    buf = rows_to_pdf(rows, spec["title"])
    from flask import send_file
    filename = f"{spec['title'].replace(' ', '_')}_{today_str()}.pdf"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def rows_to_xlsx(rows, sheet_title="Report"):
    import openpyxl
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Report")[:31]
    _write_xlsx_sheet(ws, rows)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
