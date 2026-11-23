"""purchases routes."""
import db
import os
import purchase_import
from core.helpers import compute_line_gst, get_attachments, get_company_settings, get_custom_attachments, get_custom_field_defs, get_custom_values, get_effective_columns, save_custom_fields, today_str
from datetime import datetime
from flask import flash, redirect, render_template, request, url_for



from flask import Blueprint

bp = Blueprint("purchases", __name__)


def state_code_from_gstin(gstin):
    """The first 2 digits of a GSTIN are the supplier's GST state code -
    used to work out CGST+SGST vs IGST on a purchase without needing a
    separate State field on the Suppliers table."""
    gstin = (gstin or "").strip()
    return gstin[:2] if len(gstin) >= 2 and gstin[:2].isdigit() else ""


# ---------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------


SUPPLIERS_LIST_PAGE_SIZE = 200


@bp.route("/suppliers")
def suppliers_list():
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = "(SupplierName LIKE ? OR ContactPerson LIKE ? OR Phone LIKE ? OR Email LIKE ? OR GSTIN LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like, like, like]

    total = db.query(f"SELECT COUNT(*) c FROM Suppliers WHERE {where}", params, one=True)["c"]
    total_pages = max((total + SUPPLIERS_LIST_PAGE_SIZE - 1) // SUPPLIERS_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * SUPPLIERS_LIST_PAGE_SIZE

    suppliers = db.query(f"""SELECT * FROM Suppliers WHERE {where} ORDER BY SupplierName
                          LIMIT ? OFFSET ?""", params + [SUPPLIERS_LIST_PAGE_SIZE, offset])
    return render_template("suppliers_list.html", suppliers=suppliers, columns=get_effective_columns("Supplier"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=SUPPLIERS_LIST_PAGE_SIZE)


@bp.route("/suppliers/new", methods=["GET", "POST"])
@bp.route("/suppliers/<int:sid>/edit", methods=["GET", "POST"])
def supplier_form(sid=None):
    supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (sid,), one=True) if sid else None
    if request.method == "POST":
        f = request.form
        args = (f["supplier_name"], f["contact_person"], f["phone"], f["email"], f["address"], f["gstin"],
                1 if f.get("active") else 0)
        if sid:
            db.execute("""UPDATE Suppliers SET SupplierName=?, ContactPerson=?, Phone=?, Email=?, Address=?,
                        GSTIN=?, Active=? WHERE SupplierID=?""", args + (sid,))
            save_custom_fields("Supplier", sid, f)
        else:
            new_id = db.execute("""INSERT INTO Suppliers (SupplierName, ContactPerson, Phone, Email, Address, GSTIN, Active)
                        VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Supplier", new_id, f)
        flash("Supplier saved.", "success")
        return redirect(url_for("suppliers_list"))
    custom_fields = get_custom_field_defs("Supplier")
    custom_values = get_custom_values("Supplier", sid) if sid else {}
    return render_template("supplier_form.html", supplier=supplier,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=sid, custom_attachments=get_custom_attachments("Supplier", sid))


# ---------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------


PURCHASES_LIST_PAGE_SIZE = 100


@bp.route("/purchases")
def purchases_list():
    # Same fix as sales_list(): filtered in SQL and paginated rather than
    # fetching every Purchase ever recorded into Python on every visit.
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = """(p.PONumber LIKE ? OR s.SupplierName LIKE ? OR p.InvoiceNumber LIKE ?
                 OR p.Status LIKE ? OR p.PaymentStatus LIKE ?)"""
        like = f"%{q}%"
        params = [like, like, like, like, like]

    from_clause = "FROM Purchases p JOIN Suppliers s ON s.SupplierID = p.SupplierID"
    total = db.query(f"SELECT COUNT(*) c {from_clause} WHERE {where}", params, one=True)["c"]
    total_pages = max((total + PURCHASES_LIST_PAGE_SIZE - 1) // PURCHASES_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * PURCHASES_LIST_PAGE_SIZE

    purchases = db.query(f"""SELECT p.*, s.SupplierName {from_clause} WHERE {where}
                          ORDER BY p.PurchaseDate DESC, p.PurchaseID DESC LIMIT ? OFFSET ?""",
                         params + [PURCHASES_LIST_PAGE_SIZE, offset])
    return render_template("purchases_list.html", purchases=purchases, columns=get_effective_columns("Purchase"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=PURCHASES_LIST_PAGE_SIZE)


def create_purchase(supplier_id, po_number, purchase_date, invoice_number, status, payment_status,
                     amount_paid, notes, lines, interstate_override=False, reverse_charge=False,
                     itc_eligible=True, purchase_id=None):
    """Creates a Purchase + PurchaseLines with the GST breakup computed the
    same way for every entry point (the manual purchase form, the
    vendor-file import, and the Admin-only edit screen all call this).
    `lines` is a list of (product_id, qty, unit_cost) tuples. Returns the
    PurchaseID (new, or the same one passed in). Inter-state is derived
    from the supplier's GSTIN when there is one (trusted over
    `interstate_override`); `interstate_override` is only used as a
    fallback for a supplier with no GSTIN on file.

    Pass `purchase_id` to EDIT an existing purchase in place instead of
    creating a new one: its old PurchaseLines and the InventoryTransaction
    rows that purchase originally created are removed first, then
    recreated from the new `lines`/`status` — so a corrected quantity or
    a Received->Draft status change is reflected correctly in stock,
    exactly as if the purchase had been entered this way to begin with."""
    company = get_company_settings()
    supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (supplier_id,), one=True)
    supplier_gstin = (supplier["GSTIN"] or "") if supplier else ""
    supplier_state_code = state_code_from_gstin(supplier_gstin)
    if supplier_state_code:
        is_interstate = 1 if (company["StateCode"] and supplier_state_code != company["StateCode"]) else 0
    else:
        is_interstate = 1 if interstate_override else 0

    taxable_total = cgst_total = sgst_total = igst_total = 0.0
    line_data = []
    for prod_id, qty, cost in lines:
        prod = db.query("SELECT HSNCode, GSTRate FROM Products WHERE ProductID=?", (prod_id,), one=True)
        hsn = (prod["HSNCode"] or "") if prod else ""
        gst_rate = (prod["GSTRate"] or 0) if prod else 0
        taxable_value = round(qty * cost, 2)
        gst = compute_line_gst(taxable_value, gst_rate, is_interstate)
        taxable_total += taxable_value
        cgst_total += gst["cgst_amt"]
        sgst_total += gst["sgst_amt"]
        igst_total += gst["igst_amt"]
        line_data.append((prod_id, qty, cost, taxable_value, hsn, gst_rate, gst))

    raw_total = taxable_total + cgst_total + sgst_total + igst_total
    grand_total = round(raw_total)
    round_off = round(grand_total - raw_total, 2)

    if purchase_id is None:
        purchase_id = db.execute("""INSERT INTO Purchases (PONumber, SupplierID, PurchaseDate, InvoiceNumber,
                    Status, PaymentStatus, TotalAmount, AmountPaid, Notes, SupplierGSTIN, SupplierStateCode,
                    IsInterState, TaxableAmount, CGSTAmount, SGSTAmount, IGSTAmount, RoundOff, ReverseCharge,
                    ITCEligible)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (po_number, supplier_id, purchase_date, invoice_number,
                     status, payment_status, grand_total, amount_paid, notes,
                     supplier_gstin, supplier_state_code, is_interstate,
                     round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                     round_off, 1 if reverse_charge else 0, 1 if itc_eligible else 0))
    else:
        db.execute("""UPDATE Purchases SET PONumber=?, SupplierID=?, PurchaseDate=?, InvoiceNumber=?, Status=?,
                    PaymentStatus=?, TotalAmount=?, AmountPaid=?, Notes=?, SupplierGSTIN=?, SupplierStateCode=?,
                    IsInterState=?, TaxableAmount=?, CGSTAmount=?, SGSTAmount=?, IGSTAmount=?, RoundOff=?,
                    ReverseCharge=?, ITCEligible=? WHERE PurchaseID=?""",
                   (po_number, supplier_id, purchase_date, invoice_number,
                    status, payment_status, grand_total, amount_paid, notes,
                    supplier_gstin, supplier_state_code, is_interstate,
                    round(taxable_total, 2), round(cgst_total, 2), round(sgst_total, 2), round(igst_total, 2),
                    round_off, 1 if reverse_charge else 0, 1 if itc_eligible else 0, purchase_id))
        db.execute("DELETE FROM PurchaseLines WHERE PurchaseID=?", (purchase_id,))
        db.execute("DELETE FROM InventoryTransactions WHERE RefType='Purchase' AND RefID=?", (purchase_id,))

    for prod_id, qty, cost, taxable_value, hsn, gst_rate, gst in line_data:
        db.execute("""INSERT INTO PurchaseLines (PurchaseID, ProductID, Qty, UnitCost, LineTotal, HSNCode,
                    GSTRate, TaxableValue, CGSTRate, CGSTAmount, SGSTRate, SGSTAmount, IGSTRate, IGSTAmount)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (purchase_id, prod_id, qty, cost, taxable_value, hsn, gst_rate, taxable_value,
                    gst["cgst_rate"], gst["cgst_amt"], gst["sgst_rate"], gst["sgst_amt"],
                    gst["igst_rate"], gst["igst_amt"]))
        if status == "Received":
            db.execute("""INSERT INTO InventoryTransactions (ProductID, TransactionDate, TransactionType,
                        QtyChange, RefType, RefID, Notes) VALUES (?,?,?,?,?,?,?)""",
                       (prod_id, purchase_date, "Purchase", qty, "Purchase", purchase_id, po_number))
    return purchase_id


@bp.route("/purchases/new", methods=["GET", "POST"])
def purchase_form():
    company = get_company_settings()
    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        costs = request.form.getlist("unit_cost[]")
        lines = [(int(p), float(q), float(c)) for p, q, c in zip(product_ids, qtys, costs) if p and q]

        po_number = f["po_number"] or f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        purchase_id = create_purchase(
            supplier_id=int(f["supplier_id"]), po_number=po_number, purchase_date=f["purchase_date"],
            invoice_number=f["invoice_number"], status=f["status"], payment_status=f["payment_status"],
            amount_paid=float(f["amount_paid"] or 0), notes=f.get("notes", ""), lines=lines,
            interstate_override=bool(f.get("interstate_purchase")),
            reverse_charge=bool(f.get("reverse_charge")), itc_eligible=bool(f.get("itc_eligible")))
        save_custom_fields("Purchase", purchase_id, f)
        flash(f"Purchase {po_number} recorded.", "success")
        return redirect(url_for("purchase_view", pid=purchase_id))
    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    custom_fields = get_custom_field_defs("Purchase")
    return render_template("purchase_form.html", suppliers=suppliers, products=products, today=today_str(),
                            company=company, custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={}, purchase=None, existing_lines=None)


@bp.route("/purchases/<int:pid>")
def purchase_view(pid):
    purchase = db.query("""SELECT p.*, s.SupplierName, s.Phone, s.Address FROM Purchases p
                         JOIN Suppliers s ON s.SupplierID=p.SupplierID WHERE p.PurchaseID=?""", (pid,), one=True)
    lines = db.query("""SELECT pl.*, pr.ProductName, pr.Unit FROM PurchaseLines pl
                      JOIN Products pr ON pr.ProductID=pl.ProductID WHERE pl.PurchaseID=?""", (pid,))
    custom_fields = get_custom_field_defs("Purchase")
    custom_values = get_custom_values("Purchase", pid)
    attachments = get_attachments("Purchase", pid)
    return render_template("purchase_view.html", purchase=purchase, lines=lines,
                            custom_fields=custom_fields, custom_values=custom_values,
                            cf_record_id=pid, custom_attachments=get_custom_attachments("Purchase", pid),
                            att_module="Purchase", att_record_id=pid, attachments=attachments)


@bp.route("/purchases/<int:pid>/edit", methods=["GET", "POST"])
def purchase_edit(pid):
    """Admin-only: correct a mistake on an already-saved purchase (wrong
    quantity/price/product/date/etc.) without deleting and re-entering it.
    Recomputes GST and replaces the InventoryTransaction rows this purchase
    originally created, the same way create_purchase() does for a new one."""
    company = get_company_settings()
    existing = db.query("SELECT * FROM Purchases WHERE PurchaseID=?", (pid,), one=True)
    if not existing:
        flash("Purchase not found.", "error")
        return redirect(url_for("purchases_list"))

    if request.method == "POST":
        f = request.form
        product_ids = request.form.getlist("product_id[]")
        qtys = request.form.getlist("qty[]")
        costs = request.form.getlist("unit_cost[]")
        lines = [(int(p), float(q), float(c)) for p, q, c in zip(product_ids, qtys, costs) if p and q]

        po_number = f["po_number"] or existing["PONumber"]
        create_purchase(
            supplier_id=int(f["supplier_id"]), po_number=po_number, purchase_date=f["purchase_date"],
            invoice_number=f["invoice_number"], status=f["status"], payment_status=f["payment_status"],
            amount_paid=float(f["amount_paid"] or 0), notes=f.get("notes", ""), lines=lines,
            interstate_override=bool(f.get("interstate_purchase")),
            reverse_charge=bool(f.get("reverse_charge")), itc_eligible=bool(f.get("itc_eligible")),
            purchase_id=pid)
        save_custom_fields("Purchase", pid, f)
        flash(f"Purchase {po_number} updated.", "success")
        return redirect(url_for("purchase_view", pid=pid))

    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
    existing_lines = db.query("SELECT * FROM PurchaseLines WHERE PurchaseID=?", (pid,))
    custom_fields = get_custom_field_defs("Purchase")
    custom_values = get_custom_values("Purchase", pid)
    return render_template("purchase_form.html", suppliers=suppliers, products=products,
                            today=existing["PurchaseDate"], company=company, custom_fields=custom_fields,
                            custom_values=custom_values, cf_record_id=pid, custom_attachments={},
                            purchase=existing, existing_lines=existing_lines)


# ---------------------------------------------------------------------
# Purchases: Import from Vendor File
#
# Some suppliers can export an "SO Details" spreadsheet of what they've
# shipped instead of (or alongside) sending an invoice to type up by
# hand. This is a two-step flow: upload + parse the file (this doesn't
# save anything yet), review/match each line to one of our Products (or
# create a new one), then confirm to actually create the Purchase(s).
# Matches are remembered per supplier (SupplierProductMap) so the next
# file from the same supplier mostly auto-matches.
# ---------------------------------------------------------------------


def find_supplier_product_match(supplier_id, material_code):
    """Returns a Products row already known to correspond to this
    supplier's material code, or None if there's no known match yet.
    Checks a saved SupplierProductMap first, then falls back to a
    Products.SKU that happens to equal the vendor's code."""
    mapped = db.query("""SELECT pr.* FROM SupplierProductMap m JOIN Products pr ON pr.ProductID = m.ProductID
                       WHERE m.SupplierID=? AND m.VendorCode=?""", (supplier_id, material_code), one=True)
    if mapped:
        return mapped
    return db.query("SELECT * FROM Products WHERE SKU=?", (material_code,), one=True)


@bp.route("/purchases/import", methods=["GET", "POST"])
def purchase_import_upload():
    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        file_storage = request.files.get("file")
        if not supplier_id:
            flash("Select which supplier this file is from.", "error")
            return redirect(url_for("purchase_import_upload"))
        if not file_storage or not file_storage.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("purchase_import_upload"))

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            file_storage.save(tmp.name)
            tmp_path = tmp.name
        try:
            result = purchase_import.parse_so_details(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if not result["ok"]:
            flash(result["message"], "error")
            return redirect(url_for("purchase_import_upload"))

        supplier_id = int(supplier_id)
        rows = result["rows"]
        # Group rows by SO Number (order of first appearance), so the
        # review page can show one header (PO number/date/etc.) per
        # group and create one Purchase per group on confirm.
        group_index_by_so = {}
        groups = []
        for row in rows:
            key = row["so_number"] or "(no SO number)"
            if key not in group_index_by_so:
                group_index_by_so[key] = len(groups)
                groups.append(dict(so_number=key, dates=[], rows=[]))
            g = groups[group_index_by_so[key]]
            g["rows"].append(row)
            if row["so_date"]:
                g["dates"].append(row["so_date"])

        for g in groups:
            g["purchase_date"] = max(g["dates"]) if g["dates"] else today_str()
            g["suggested_po_number"] = f"SO-{g['so_number']}" if g["so_number"] != "(no SO number)" \
                else f"IMPORT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Attach a match (or None) to every row, a group index, and a stable
        # global row index (0..len(rows)-1) that the review template uses to
        # name each row's form fields, since rows are also re-grouped by SO
        # Number for display.
        for i, row in enumerate(rows):
            row["group_index"] = group_index_by_so[row["so_number"] or "(no SO number)"]
            row["match"] = find_supplier_product_match(supplier_id, row["material_code"])
            row["global_index"] = i

        supplier = db.query("SELECT * FROM Suppliers WHERE SupplierID=?", (supplier_id,), one=True)
        products = db.query("SELECT * FROM Products WHERE Active=1 ORDER BY ProductName")
        matched_count = sum(1 for row in rows if row["match"])
        flash(f"{result['message']} {matched_count} of {len(rows)} already matched to a known product "
              f"— review the rest below before confirming.", "success")
        return render_template("purchase_import_review.html", supplier=supplier, groups=groups, rows=rows,
                                products=products, today=today_str())

    suppliers = db.query("SELECT * FROM Suppliers WHERE Active=1 ORDER BY SupplierName")
    return render_template("purchase_import_upload.html", suppliers=suppliers)


@bp.route("/purchases/import/confirm", methods=["POST"])
def purchase_import_confirm():
    f = request.form
    supplier_id = int(f["supplier_id"])
    row_count = int(f.get("row_count") or 0)
    group_count = int(f.get("group_count") or 0)

    # Build each group's header info and an empty line list first.
    group_lines = [[] for _ in range(group_count)]
    group_info = []
    for g in range(group_count):
        group_info.append(dict(
            po_number=(f.get(f"po_number_{g}") or "").strip() or f"IMPORT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{g}",
            purchase_date=f.get(f"purchase_date_{g}") or today_str(),
            payment_status=f.get(f"payment_status_{g}") or "Unpaid",
            amount_paid=float(f.get(f"amount_paid_{g}") or 0),
            notes=f.get(f"notes_{g}") or "",
            reverse_charge=bool(f.get(f"reverse_charge_{g}")),
            itc_eligible=bool(f.get(f"itc_eligible_{g}")),
            interstate_override=bool(f.get(f"interstate_override_{g}")),
        ))

    created_purchase_ids = []
    skipped = 0
    new_products_created = 0
    for i in range(row_count):
        group_idx = int(f.get(f"row_group_{i}", 0))
        material_code = f.get(f"row_material_code_{i}", "")
        material_name = f.get(f"row_material_name_{i}", "")
        qty = float(f.get(f"row_qty_{i}") or 0)
        unit_cost = float(f.get(f"row_unit_cost_{i}") or 0)
        if qty <= 0:
            skipped += 1
            continue
        product_id_raw = f.get(f"row_product_id_{i}", "")
        remember = bool(f.get(f"row_remember_{i}"))

        if product_id_raw == "__new__" or not product_id_raw:
            new_name = (f.get(f"row_new_product_name_{i}") or material_name or material_code).strip()
            sku_candidate = material_code
            # Selling price isn't in a vendor purchase file, so default it to the purchase cost
            # rather than 0 - a new product should have a usable price right away (e.g. when
            # issuing stock to a salesperson), not a ₹0 price that's easy to miss and correct later
            # on the Products page.
            default_selling_price = unit_cost
            product_id = None
            # Try the material code as-is, then a supplier-prefixed variant, then keep appending a
            # counter until an unused SKU is found - covers both a collision against an existing
            # product AND two rows in the same file sharing a material code that both need a new
            # product (each previous attempt in that case fails on the exact same fallback string).
            candidates = [sku_candidate, f"S{supplier_id}-{material_code}"]
            n = 2
            while product_id is None:
                if not candidates:
                    candidates = [f"S{supplier_id}-{material_code}-{n}"]
                    n += 1
                sku_candidate = candidates.pop(0)
                try:
                    product_id = db.execute("""INSERT INTO Products (SKU, ProductName, Unit, CostPrice, SellingPrice,
                                DefaultSupplierID, Active) VALUES (?,?,?,?,?,?,1)""",
                                (sku_candidate, new_name, "PCS", unit_cost, default_selling_price, supplier_id))
                except Exception:
                    continue  # SKU already taken - try the next candidate
            remember = True  # always remember a brand-new product's mapping, so it isn't recreated next time
            new_products_created += 1
        else:
            product_id = int(product_id_raw)

        if remember and material_code:
            db.execute("""INSERT INTO SupplierProductMap (SupplierID, VendorCode, VendorName, ProductID)
                        VALUES (?,?,?,?)
                        ON CONFLICT(SupplierID, VendorCode) DO UPDATE SET ProductID=excluded.ProductID,
                        VendorName=excluded.VendorName""",
                       (supplier_id, material_code, material_name, product_id))

        group_lines[group_idx].append((product_id, qty, unit_cost))

    for g, lines in enumerate(group_lines):
        if not lines:
            continue
        info = group_info[g]
        purchase_id = create_purchase(
            supplier_id=supplier_id, po_number=info["po_number"], purchase_date=info["purchase_date"],
            invoice_number="", status="Received", payment_status=info["payment_status"],
            amount_paid=info["amount_paid"], notes=info["notes"] or "Imported from vendor file", lines=lines,
            interstate_override=info["interstate_override"], reverse_charge=info["reverse_charge"],
            itc_eligible=info["itc_eligible"])
        created_purchase_ids.append(purchase_id)

    if not created_purchase_ids:
        flash("Nothing to import — every line had zero quantity.", "error")
        return redirect(url_for("purchase_import_upload"))

    msg = f"Imported {len(created_purchase_ids)} purchase(s) from the file."
    if skipped:
        msg += f" ({skipped} zero-quantity line(s) skipped.)"
    flash(msg, "success")
    if new_products_created:
        flash(f"{new_products_created} new product(s) were created with their selling price defaulted to their "
              f"purchase cost (no markup) — review and update pricing on the Inventory page before selling them.",
              "warning")
    if len(created_purchase_ids) == 1:
        return redirect(url_for("purchase_view", pid=created_purchase_ids[0]))
    return redirect(url_for("purchases_list"))


# ---------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------


EXPENSE_CATEGORIES = ["Rent", "Utilities", "Office Supplies", "Fuel", "Toll",
                       "Loading/Unloading", "Salaries (misc)", "Repairs", "Insurance", "Other"]


@bp.route("/expenses")
def expenses_list():
    expenses = db.query("""SELECT e.*, v.RegistrationNumber FROM Expenses e
                         LEFT JOIN Vehicles v ON v.VehicleID=e.VehicleID ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC""")
    return render_template("expenses_list.html", expenses=expenses, categories=EXPENSE_CATEGORIES,
                            columns=get_effective_columns("Expense"))


@bp.route("/expenses/new", methods=["GET", "POST"])
@bp.route("/expenses/<int:eid>/edit", methods=["GET", "POST"])
def expense_form(eid=None):
    expense = db.query("SELECT * FROM Expenses WHERE ExpenseID=?", (eid,), one=True) if eid else None
    if request.method == "POST":
        f = request.form
        args = (f["expense_date"], f["category"], int(f["vehicle_id"]) if f.get("vehicle_id") else None,
                float(f["amount"]), f["payment_mode"], f["paid_to"], f.get("description", ""))
        if eid:
            db.execute("""UPDATE Expenses SET ExpenseDate=?, Category=?, VehicleID=?, Amount=?, PaymentMode=?,
                        PaidTo=?, Description=? WHERE ExpenseID=?""", args + (eid,))
            save_custom_fields("Expense", eid, f)
        else:
            new_id = db.execute("""INSERT INTO Expenses (ExpenseDate, Category, VehicleID, Amount, PaymentMode, PaidTo,
                        Description) VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Expense", new_id, f)
        flash("Expense saved.", "success")
        return redirect(url_for("expenses_list"))
    vehicles = db.query("SELECT * FROM Vehicles ORDER BY RegistrationNumber")
    custom_fields = get_custom_field_defs("Expense")
    custom_values = get_custom_values("Expense", eid) if eid else {}
    attachments = get_attachments("Expense", eid) if eid else []
    return render_template("expense_form.html", expense=expense, vehicles=vehicles,
                            categories=EXPENSE_CATEGORIES, today=today_str(),
                            cf_record_id=eid, custom_attachments=get_custom_attachments("Expense", eid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Expense", att_record_id=eid, attachments=attachments)
