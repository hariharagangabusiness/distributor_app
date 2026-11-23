"""payroll routes."""
import calendar
import db
from core.auth import admin_required
from core.helpers import MONTH_NAMES, amount_in_words, get_attachments, get_company_settings, get_custom_attachments, get_custom_field_defs, get_custom_values, get_effective_columns, save_custom_fields, today_str
from datetime import date, datetime
from flask import flash, redirect, render_template, request, url_for



from flask import Blueprint

bp = Blueprint("payroll", __name__)


# ---------------------------------------------------------------------
# Vehicles
# ---------------------------------------------------------------------


VEHICLES_LIST_PAGE_SIZE = 200


@bp.route("/vehicles")
def vehicles_list():
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = "(RegistrationNumber LIKE ? OR VehicleType LIKE ? OR Make LIKE ? OR Model LIKE ? OR Status LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like, like, like]

    total = db.query(f"SELECT COUNT(*) c FROM Vehicles WHERE {where}", params, one=True)["c"]
    total_pages = max((total + VEHICLES_LIST_PAGE_SIZE - 1) // VEHICLES_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * VEHICLES_LIST_PAGE_SIZE

    vehicles = db.query(f"""SELECT * FROM Vehicles WHERE {where} ORDER BY RegistrationNumber
                         LIMIT ? OFFSET ?""", params + [VEHICLES_LIST_PAGE_SIZE, offset])
    return render_template("vehicles_list.html", vehicles=vehicles, today=today_str(), columns=get_effective_columns("Vehicle"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=VEHICLES_LIST_PAGE_SIZE)


@bp.route("/vehicles/new", methods=["GET", "POST"])
@bp.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
def vehicle_form(vid=None):
    vehicle = db.query("SELECT * FROM Vehicles WHERE VehicleID=?", (vid,), one=True) if vid else None
    if request.method == "POST":
        f = request.form
        args = (f["registration_number"], f["vehicle_type"], f["make"], f["model"],
                f.get("purchase_date") or None,
                float(f["purchase_price"]) if f.get("purchase_price") else None,
                f.get("insurance_expiry") or None,
                f.get("permit_expiry") or None, f.get("puc_expiry") or None,
                f.get("fitness_expiry") or None, float(f["current_odometer"] or 0), f["status"])
        if vid:
            db.execute("""UPDATE Vehicles SET RegistrationNumber=?, VehicleType=?, Make=?, Model=?,
                        PurchaseDate=?, PurchasePrice=?, InsuranceExpiry=?, PermitExpiry=?, PUCExpiry=?, FitnessExpiry=?,
                        CurrentOdometer=?, Status=? WHERE VehicleID=?""", args + (vid,))
            save_custom_fields("Vehicle", vid, f)
        else:
            new_id = db.execute("""INSERT INTO Vehicles (RegistrationNumber, VehicleType, Make, Model, PurchaseDate,
                        PurchasePrice, InsuranceExpiry, PermitExpiry, PUCExpiry, FitnessExpiry, CurrentOdometer, Status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", args)
            save_custom_fields("Vehicle", new_id, f)
        flash("Vehicle saved.", "success")
        return redirect(url_for("vehicles_list"))
    custom_fields = get_custom_field_defs("Vehicle")
    custom_values = get_custom_values("Vehicle", vid) if vid else {}
    attachments = get_attachments("Vehicle", vid) if vid else []
    return render_template("vehicle_form.html", vehicle=vehicle,
                            cf_record_id=vid, custom_attachments=get_custom_attachments("Vehicle", vid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Vehicle", att_record_id=vid, attachments=attachments)


# ---------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------


def get_active_leave_types():
    return db.query("SELECT * FROM LeaveTypes WHERE Active=1 ORDER BY DisplayOrder, LeaveTypeID")


EMPLOYEES_LIST_PAGE_SIZE = 200


@bp.route("/employees")
def employees_list():
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = "(EmployeeName LIKE ? OR Designation LIKE ? OR Phone LIKE ? OR Status LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like, like]

    total = db.query(f"SELECT COUNT(*) c FROM Employees WHERE {where}", params, one=True)["c"]
    total_pages = max((total + EMPLOYEES_LIST_PAGE_SIZE - 1) // EMPLOYEES_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * EMPLOYEES_LIST_PAGE_SIZE

    employees = db.query(f"""SELECT * FROM Employees WHERE {where} ORDER BY EmployeeName
                          LIMIT ? OFFSET ?""", params + [EMPLOYEES_LIST_PAGE_SIZE, offset])
    return render_template("employees_list.html", employees=employees, columns=get_effective_columns("Employee"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=EMPLOYEES_LIST_PAGE_SIZE)


@bp.route("/employees/new", methods=["GET", "POST"])
@bp.route("/employees/<int:eid>/edit", methods=["GET", "POST"])
def employee_form(eid=None):
    employee = db.query("SELECT * FROM Employees WHERE EmployeeID=?", (eid,), one=True) if eid else None
    if request.method == "POST":
        f = request.form
        args = (f["employee_name"], f["designation"], f["phone"], f.get("join_date") or None,
                float(f["monthly_salary"] or 0), f["bank_account"], f["status"])
        if eid:
            db.execute("""UPDATE Employees SET EmployeeName=?, Designation=?, Phone=?, JoinDate=?,
                        MonthlySalary=?, BankAccount=?, Status=? WHERE EmployeeID=?""", args + (eid,))
            save_custom_fields("Employee", eid, f)
        else:
            new_id = db.execute("""INSERT INTO Employees (EmployeeName, Designation, Phone, JoinDate, MonthlySalary,
                        BankAccount, Status) VALUES (?,?,?,?,?,?,?)""", args)
            save_custom_fields("Employee", new_id, f)
        flash("Employee saved.", "success")
        return redirect(url_for("employees_list"))
    custom_fields = get_custom_field_defs("Employee")
    custom_values = get_custom_values("Employee", eid) if eid else {}
    attachments = get_attachments("Employee", eid) if eid else []
    return render_template("employee_form.html", employee=employee,
                            cf_record_id=eid, custom_attachments=get_custom_attachments("Employee", eid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Employee", att_record_id=eid, attachments=attachments)


# ---------------------------------------------------------------------
# Salary (monthly schedule)
# ---------------------------------------------------------------------


@bp.route("/salary")
def salary_month():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    records = db.query("""SELECT sp.*, e.EmployeeName, e.Designation FROM SalaryPayments sp
                        JOIN Employees e ON e.EmployeeID=sp.EmployeeID
                        WHERE sp.SalaryYear=? AND sp.SalaryMonth=? ORDER BY e.EmployeeName""", (year, month))
    generated_ids = {r["EmployeeID"] for r in records}
    missing = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    missing = [m for m in missing if m["EmployeeID"] not in generated_ids]
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("salary_month.html", records=records, missing=missing, year=year, month=month,
                            month_name=MONTH_NAMES[month], prev_month=prev_month, prev_year=prev_year,
                            next_month=next_month, next_year=next_year, today=today_str(),
                            columns=get_effective_columns("SalaryPayment"))


def compute_salary_figures(e, year, month):
    """Computes one employee's pay for a given month: gross pro-rated by
    days actually worked (days in month, minus any days before their Join
    Date if they joined mid-month, minus loss-of-pay days from the Leave &
    Attendance tab), minus the currently-active advance deduction. Used by
    both salary_generate() (first-time schedule creation) and
    salary_refresh() (re-pull advances / recompute for a Pending row)."""
    gross = e["MonthlySalary"]
    days_in_month = calendar.monthrange(year, month)[1]

    # Loss-of-pay: only applied if attendance was actually recorded for this
    # employee/month (Leave & Attendance tab). No attendance entry -> 0 LOP
    # days, same behavior as before attendance tracking existed.
    att = db.query("SELECT * FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                   (e["EmployeeID"], year, month), one=True)
    lop_days = att["LOPDays"] if att else 0.0
    if att and att["DaysInMonth"]:
        days_in_month = att["DaysInMonth"]

    # Days before Join Date: if the employee joined partway through this
    # month, don't pay for the days before they joined. If they haven't
    # joined by the end of this month at all, payable days is 0.
    days_before_join = 0.0
    join_date_str = e["JoinDate"]
    if join_date_str:
        try:
            jd = datetime.strptime(join_date_str[:10], "%Y-%m-%d").date()
            month_start = date(year, month, 1)
            month_end = date(year, month, calendar.monthrange(year, month)[1])
            if jd > month_end:
                days_before_join = days_in_month  # not yet joined this month - 0 payable days
            elif jd > month_start:
                days_before_join = (jd - month_start).days  # join day itself still counts as worked
        except ValueError:
            pass

    per_day = (gross / days_in_month) if days_in_month else 0
    payable_days = max(days_in_month - days_before_join - lop_days, 0)
    basic = round(per_day * payable_days, 2)

    advances = db.query("SELECT * FROM AdvancePayments WHERE EmployeeID=? AND Status='Active'", (e["EmployeeID"],))
    advance_deduct = round(sum(min(a["MonthlyDeduction"], a["BalanceRemaining"]) for a in advances), 2)
    net = round(basic - advance_deduct, 2)
    return dict(gross=gross, lop_days=lop_days, days_before_join=days_before_join,
                days_in_month=days_in_month, basic=basic, advance_deduct=advance_deduct, net=net)


@bp.route("/salary/generate", methods=["POST"])
def salary_generate():
    year = int(request.form["year"])
    month = int(request.form["month"])
    employees = db.query("SELECT * FROM Employees WHERE Status='Active'")
    created = 0
    for e in employees:
        exists = db.query("SELECT 1 FROM SalaryPayments WHERE EmployeeID=? AND SalaryYear=? AND SalaryMonth=?",
                           (e["EmployeeID"], year, month), one=True)
        if exists:
            continue
        fig = compute_salary_figures(e, year, month)
        db.execute("""INSERT INTO SalaryPayments (EmployeeID, SalaryYear, SalaryMonth, GrossSalary, LOPDays,
                    BasicAmount, Bonus, Deductions, AdvanceDeducted, NetPayable, Status)
                    VALUES (?,?,?,?,?,?,0,0,?,?,?)""",
                   (e["EmployeeID"], year, month, fig["gross"], fig["lop_days"], fig["basic"],
                    fig["advance_deduct"], fig["net"], "Pending"))
        created += 1
    flash(f"Salary schedule generated for {MONTH_NAMES[month]} {year}"
          f"{f' ({created} new record(s))' if created else ' (nothing new — all employees already have a record)'}.",
          "success")
    return redirect(url_for("salary_month", year=year, month=month))


@bp.route("/salary/refresh", methods=["POST"])
def salary_refresh():
    """Re-pulls active advances and recalculates pro-rated pay (Join Date,
    attendance, leave policy) for every Pending salary row this month, and
    creates any still-missing rows too (same as Generate) — use this after
    giving an advance or updating attendance so the schedule reflects it
    before anyone is marked Paid. Rows already marked Paid are left alone,
    since that money has already gone out the door."""
    year = int(request.form["year"])
    month = int(request.form["month"])
    employees = db.query("SELECT * FROM Employees WHERE Status='Active'")
    updated = created = skipped_paid = 0
    for e in employees:
        existing = db.query("SELECT * FROM SalaryPayments WHERE EmployeeID=? AND SalaryYear=? AND SalaryMonth=?",
                            (e["EmployeeID"], year, month), one=True)
        if existing and existing["Status"] == "Paid":
            skipped_paid += 1
            continue
        fig = compute_salary_figures(e, year, month)
        if existing:
            db.execute("""UPDATE SalaryPayments SET GrossSalary=?, LOPDays=?, BasicAmount=?, AdvanceDeducted=?,
                        NetPayable=? WHERE PaymentID=?""",
                       (fig["gross"], fig["lop_days"], fig["basic"], fig["advance_deduct"], fig["net"],
                        existing["PaymentID"]))
            updated += 1
        else:
            db.execute("""INSERT INTO SalaryPayments (EmployeeID, SalaryYear, SalaryMonth, GrossSalary, LOPDays,
                        BasicAmount, Bonus, Deductions, AdvanceDeducted, NetPayable, Status)
                        VALUES (?,?,?,?,?,?,0,0,?,?,?)""",
                       (e["EmployeeID"], year, month, fig["gross"], fig["lop_days"], fig["basic"],
                        fig["advance_deduct"], fig["net"], "Pending"))
            created += 1
    msg = f"Refreshed {MONTH_NAMES[month]} {year}: {updated} updated, {created} newly added"
    if skipped_paid:
        msg += f", {skipped_paid} already Paid (left as-is)"
    flash(msg + ".", "success")
    return redirect(url_for("salary_month", year=year, month=month))


@bp.route("/salary/<int:pid>/pay", methods=["POST"])
def salary_pay(pid):
    payment = db.query("SELECT * FROM SalaryPayments WHERE PaymentID=?", (pid,), one=True)
    db.execute("""UPDATE SalaryPayments SET Status='Paid', PaymentDate=?, PaymentMode=? WHERE PaymentID=?""",
               (request.form.get("payment_date") or today_str(), request.form.get("payment_mode", "Bank"), pid))
    if payment["AdvanceDeducted"] > 0:
        remaining_to_apply = payment["AdvanceDeducted"]
        advances = db.query("""SELECT * FROM AdvancePayments WHERE EmployeeID=? AND Status='Active'
                             ORDER BY AdvanceDate""", (payment["EmployeeID"],))
        for a in advances:
            if remaining_to_apply <= 0:
                break
            deduct = min(a["MonthlyDeduction"], a["BalanceRemaining"], remaining_to_apply)
            new_balance = a["BalanceRemaining"] - deduct
            status = "Closed" if new_balance <= 0.0001 else "Active"
            db.execute("UPDATE AdvancePayments SET BalanceRemaining=?, Status=? WHERE AdvanceID=?",
                       (max(new_balance, 0), status, a["AdvanceID"]))
            remaining_to_apply -= deduct
    flash("Salary marked as paid.", "success")
    return redirect(url_for("salary_month", year=payment["SalaryYear"], month=payment["SalaryMonth"]))


def _get_payslip_context(pid):
    payment = db.query("SELECT * FROM SalaryPayments WHERE PaymentID=?", (pid,), one=True)
    if not payment:
        return None
    employee = db.query("SELECT * FROM Employees WHERE EmployeeID=?", (payment["EmployeeID"],), one=True)
    company = get_company_settings()
    month_name = MONTH_NAMES[payment["SalaryMonth"]]
    words = amount_in_words(payment["NetPayable"])

    # Days worked / leaves taken: pulled from the Leave & Attendance tab
    # (AttendanceMonthly) for this employee/month when it exists; falls
    # back to deriving "days worked" from the salary figures themselves
    # (days in month minus LOP days) when no attendance entry was recorded,
    # in which case leave days simply show as 0/not recorded.
    att = db.query("SELECT * FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                   (payment["EmployeeID"], payment["SalaryYear"], payment["SalaryMonth"]), one=True)
    leave_details = []
    if att:
        days_in_month = att["DaysInMonth"]
        days_worked = att["PresentDays"]
        leave_days_total = db.query("""SELECT COALESCE(SUM(DaysTaken),0) AS total FROM AttendanceLeaveDetail
                                     WHERE AttendanceID=?""", (att["AttendanceID"],), one=True)["total"]
        leave_details = db.query("""SELECT lt.LeaveTypeName, lt.LeaveCode, ld.DaysTaken
                                  FROM AttendanceLeaveDetail ld JOIN LeaveTypes lt ON lt.LeaveTypeID=ld.LeaveTypeID
                                  WHERE ld.AttendanceID=? AND ld.DaysTaken>0 ORDER BY lt.DisplayOrder""",
                                  (att["AttendanceID"],))
    else:
        days_in_month = calendar.monthrange(payment["SalaryYear"], payment["SalaryMonth"])[1]
        days_worked = max(days_in_month - payment["LOPDays"], 0)
        leave_days_total = 0

    return dict(payment=payment, employee=employee, company=company, month_name=month_name, amount_words=words,
                days_in_month=days_in_month, days_worked=days_worked, leave_days_total=leave_days_total,
                leave_details=leave_details, attendance_recorded=bool(att))


@bp.route("/salary/<int:pid>/payslip")
def payslip_view(pid):
    ctx = _get_payslip_context(pid)
    if not ctx:
        flash("Salary record not found.", "error")
        return redirect(url_for("salary_month"))
    return render_template("payslip.html", **ctx)


@bp.route("/salary/<int:pid>/payslip.pdf")
def payslip_pdf_download(pid):
    ctx = _get_payslip_context(pid)
    if not ctx:
        flash("Salary record not found.", "error")
        return redirect(url_for("salary_month"))
    from payslip_pdf import build_payslip_pdf
    pdf_bytes = build_payslip_pdf(ctx["payment"], ctx["employee"], ctx["company"], ctx["month_name"], ctx["amount_words"],
                                   days_in_month=ctx["days_in_month"], days_worked=ctx["days_worked"],
                                   leave_days_total=ctx["leave_days_total"], leave_details=ctx["leave_details"],
                                   attendance_recorded=ctx["attendance_recorded"])
    from flask import Response
    safe_name = f"{ctx['employee']['EmployeeName']}-{ctx['month_name']}-{ctx['payment']['SalaryYear']}".replace(" ", "_").replace("/", "-")
    return Response(pdf_bytes, mimetype="application/pdf",
                     headers={"Content-Disposition": f'inline; filename="Payslip-{safe_name}.pdf"'})


# ---------------------------------------------------------------------
# Advance payments
# ---------------------------------------------------------------------


ADVANCES_LIST_PAGE_SIZE = 100


@bp.route("/advances")
def advances_list():
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    where = "1=1"
    params = []
    if q:
        where = "(e.EmployeeName LIKE ? OR a.Reason LIKE ? OR a.Status LIKE ?)"
        like = f"%{q}%"
        params = [like, like, like]

    total = db.query(f"""SELECT COUNT(*) c FROM AdvancePayments a
                       JOIN Employees e ON e.EmployeeID=a.EmployeeID WHERE {where}""", params, one=True)["c"]
    total_pages = max((total + ADVANCES_LIST_PAGE_SIZE - 1) // ADVANCES_LIST_PAGE_SIZE, 1)
    page = min(page, total_pages)
    offset = (page - 1) * ADVANCES_LIST_PAGE_SIZE

    advances = db.query(f"""SELECT a.*, e.EmployeeName FROM AdvancePayments a
                         JOIN Employees e ON e.EmployeeID=a.EmployeeID WHERE {where} ORDER BY a.AdvanceDate DESC
                         LIMIT ? OFFSET ?""", params + [ADVANCES_LIST_PAGE_SIZE, offset])
    return render_template("advances_list.html", advances=advances, columns=get_effective_columns("Advance"),
                            q=q, page=page, total_pages=total_pages, total=total, page_size=ADVANCES_LIST_PAGE_SIZE)


@bp.route("/advances/new", methods=["GET", "POST"])
def advance_form():
    if request.method == "POST":
        f = request.form
        amount = float(f["amount"])
        months = max(int(f["repayment_months"] or 1), 1)
        monthly = round(amount / months, 2)
        new_id = db.execute("""INSERT INTO AdvancePayments (EmployeeID, AdvanceDate, Amount, Reason, RepaymentMonths,
                    MonthlyDeduction, BalanceRemaining, Status) VALUES (?,?,?,?,?,?,?,?)""",
                   (int(f["employee_id"]), f["advance_date"], amount, f.get("reason", ""), months,
                    monthly, amount, "Active"))
        save_custom_fields("Advance", new_id, f)
        flash("Advance payment recorded.", "success")
        return redirect(url_for("advance_view", aid=new_id))
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    custom_fields = get_custom_field_defs("Advance")
    return render_template("advance_form.html", employees=employees, today=today_str(),
                            custom_fields=custom_fields, custom_values={},
                            cf_record_id=None, custom_attachments={})


@bp.route("/advances/<int:aid>")
def advance_view(aid):
    advance = db.query("""SELECT a.*, e.EmployeeName, e.Designation, e.Phone FROM AdvancePayments a
                        JOIN Employees e ON e.EmployeeID=a.EmployeeID WHERE a.AdvanceID=?""", (aid,), one=True)
    if not advance:
        flash("Advance not found.", "error")
        return redirect(url_for("advances_list"))
    custom_fields = get_custom_field_defs("Advance")
    custom_values = get_custom_values("Advance", aid)
    attachments = get_attachments("Advance", aid)
    return render_template("advance_view.html", advance=advance,
                            cf_record_id=aid, custom_attachments=get_custom_attachments("Advance", aid),
                            custom_fields=custom_fields, custom_values=custom_values,
                            att_module="Advance", att_record_id=aid, attachments=attachments)


@bp.route("/advances/<int:aid>/close", methods=["POST"])
def advance_close(aid):
    db.execute("UPDATE AdvancePayments SET Status='Closed', BalanceRemaining=0 WHERE AdvanceID=?", (aid,))
    flash("Advance closed.", "success")
    return redirect(request.form.get("return_to") or url_for("advances_list"))


# =======================================================================
# EMPLOYEE LEAVE & ATTENDANCE
# =======================================================================
# Attendance is entered as one MONTHLY SUMMARY per employee (present days,
# weekly-off/holiday days, and days taken per configured leave type) —
# not a daily calendar log. Each leave type's days are automatically split
# into Paid (covered by the SHARED remaining annual quota for that leave
# type — the same quota every employee draws from, tracked per-employee
# only in terms of how much each one has used) and Unpaid the moment the
# month is saved; Loss-of-Pay days = days in month - present - weekly off
# - paid leave, folded straight into that month's salary generation as a
# per-day deduction.


def get_shared_leave_quotas():
    """dict LeaveTypeID -> AnnualQuota. Quotas are shared: every employee
    draws from the same annual quota for a given leave type, configured
    once on the Leave Types page rather than per employee."""
    rows = db.query("SELECT LeaveTypeID, AnnualQuota FROM LeaveTypes")
    return {r["LeaveTypeID"]: r["AnnualQuota"] for r in rows}


def get_leave_paid_used_before(employee_id, leave_type_id, year, month):
    """Total PAID leave days already used this calendar year, strictly
    before the given month — i.e. how much of the annual quota is left
    to draw on when this month is saved."""
    r = db.query("""SELECT COALESCE(SUM(ald.PaidDays),0) AS u FROM AttendanceLeaveDetail ald
                  JOIN AttendanceMonthly am ON am.AttendanceID = ald.AttendanceID
                  WHERE am.EmployeeID=? AND ald.LeaveTypeID=? AND am.AttYear=? AND am.AttMonth<?""",
                 (employee_id, leave_type_id, year, month), one=True)
    return r["u"] or 0.0


def get_leave_used_in_year(employee_id, leave_type_id, year):
    r = db.query("""SELECT COALESCE(SUM(ald.PaidDays),0) AS u FROM AttendanceLeaveDetail ald
                  JOIN AttendanceMonthly am ON am.AttendanceID = ald.AttendanceID
                  WHERE am.EmployeeID=? AND ald.LeaveTypeID=? AND am.AttYear=?""",
                 (employee_id, leave_type_id, year), one=True)
    return r["u"] or 0.0


def get_leave_balance_asof(employee_id, year, month):
    """dict LeaveTypeID -> {quota, used_before, remaining_before} as of
    the start of the given month (used to show a reference balance while
    entering that month's attendance)."""
    quotas = get_shared_leave_quotas()
    out = {}
    for lt in get_active_leave_types():
        ltid = lt["LeaveTypeID"]
        used = get_leave_paid_used_before(employee_id, ltid, year, month)
        quota = quotas.get(ltid, 0)
        out[ltid] = dict(quota=quota, used_before=used, remaining_before=max(quota - used, 0))
    return out


def get_attendance_month(employee_id, year, month):
    return db.query("SELECT * FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                    (employee_id, year, month), one=True)


def get_attendance_leave_detail(attendance_id):
    return db.query("""SELECT ald.*, lt.LeaveTypeName, lt.LeaveCode FROM AttendanceLeaveDetail ald
                     JOIN LeaveTypes lt ON lt.LeaveTypeID = ald.LeaveTypeID WHERE ald.AttendanceID=?""",
                    (attendance_id,))


def save_attendance(employee_id, year, month, present_days, weekly_off_days, leave_inputs, notes):
    """leave_inputs: dict {LeaveTypeID: days_taken}. Computes each leave
    type's paid/unpaid split against the employee's remaining annual quota
    (quota minus what they've already used earlier in the same calendar
    year), locks that split in, and derives the month's LOPDays. Returns
    the computed LOPDays."""
    days_in_month = calendar.monthrange(year, month)[1]
    quotas = get_shared_leave_quotas()

    total_paid_leave = 0.0
    detail_rows = []
    for leave_type_id, days_taken in leave_inputs.items():
        if days_taken <= 0:
            continue
        prior_used = get_leave_paid_used_before(employee_id, leave_type_id, year, month)
        quota = quotas.get(leave_type_id, 0)
        remaining = max(quota - prior_used, 0)
        paid = min(days_taken, remaining)
        unpaid = days_taken - paid
        total_paid_leave += paid
        detail_rows.append((leave_type_id, days_taken, paid, unpaid))

    lop_days = max(days_in_month - present_days - weekly_off_days - total_paid_leave, 0)

    existing = db.query("SELECT AttendanceID FROM AttendanceMonthly WHERE EmployeeID=? AND AttYear=? AND AttMonth=?",
                        (employee_id, year, month), one=True)
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        attendance_id = existing["AttendanceID"]
        db.execute("""UPDATE AttendanceMonthly SET DaysInMonth=?, PresentDays=?, WeeklyOffDays=?, LOPDays=?,
                    Notes=?, UpdatedAt=? WHERE AttendanceID=?""",
                   (days_in_month, present_days, weekly_off_days, lop_days, notes, now, attendance_id))
        db.execute("DELETE FROM AttendanceLeaveDetail WHERE AttendanceID=?", (attendance_id,))
    else:
        attendance_id = db.execute("""INSERT INTO AttendanceMonthly (EmployeeID, AttYear, AttMonth, DaysInMonth,
                    PresentDays, WeeklyOffDays, LOPDays, Notes, UpdatedAt) VALUES (?,?,?,?,?,?,?,?,?)""",
                   (employee_id, year, month, days_in_month, present_days, weekly_off_days, lop_days, notes, now))

    for leave_type_id, days_taken, paid, unpaid in detail_rows:
        db.execute("""INSERT INTO AttendanceLeaveDetail (AttendanceID, LeaveTypeID, DaysTaken, PaidDays, UnpaidDays)
                    VALUES (?,?,?,?,?)""", (attendance_id, leave_type_id, days_taken, paid, unpaid))

    return lop_days


@bp.route("/attendance")
def attendance_month():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    days_in_month = calendar.monthrange(year, month)[1]
    leave_types = get_active_leave_types()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")

    rows = []
    for e in employees:
        att = get_attendance_month(e["EmployeeID"], year, month)
        if att:
            detail = {d["LeaveTypeID"]: d["DaysTaken"] for d in get_attendance_leave_detail(att["AttendanceID"])}
            present_days, weekly_off_days, lop_days, notes = att["PresentDays"], att["WeeklyOffDays"], att["LOPDays"], att["Notes"] or ""
            saved = True
        else:
            detail = {}
            present_days, weekly_off_days, lop_days, notes = days_in_month, 0, 0, ""
            saved = False
        balance = get_leave_balance_asof(e["EmployeeID"], year, month)
        rows.append(dict(employee=e, present_days=present_days, weekly_off_days=weekly_off_days,
                          lop_days=lop_days, notes=notes, saved=saved, leave_days=detail, balance=balance))

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return render_template("attendance_month.html", rows=rows, leave_types=leave_types, year=year, month=month,
                            month_name=MONTH_NAMES[month], days_in_month=days_in_month,
                            prev_month=prev_month, prev_year=prev_year, next_month=next_month, next_year=next_year)


@bp.route("/attendance/save", methods=["POST"])
def attendance_save():
    f = request.form
    employee_id = int(f["employee_id"])
    year = int(f["year"])
    month = int(f["month"])
    try:
        present_days = float(f.get("present_days") or 0)
        weekly_off_days = float(f.get("weekly_off_days") or 0)
    except ValueError:
        flash("Present/weekly-off days must be numbers.", "error")
        return redirect(url_for("attendance_month", year=year, month=month))

    leave_inputs = {}
    for lt in get_active_leave_types():
        raw = f.get(f"leave_{lt['LeaveTypeID']}", "")
        try:
            leave_inputs[lt["LeaveTypeID"]] = float(raw) if raw else 0.0
        except ValueError:
            leave_inputs[lt["LeaveTypeID"]] = 0.0

    lop_days = save_attendance(employee_id, year, month, present_days, weekly_off_days,
                                leave_inputs, f.get("notes", ""))
    if lop_days > 0:
        flash(f"Attendance saved — {lop_days:g} loss-of-pay day(s) will be deducted when salary is generated.", "success")
    else:
        flash("Attendance saved.", "success")
    return redirect(url_for("attendance_month", year=year, month=month))


@bp.route("/attendance/leave-types")
def leave_types_admin():
    leave_types = db.query("SELECT * FROM LeaveTypes ORDER BY DisplayOrder, LeaveTypeID")
    return render_template("leave_types_admin.html", leave_types=leave_types)


@bp.route("/attendance/leave-types/add", methods=["POST"])
def leave_type_add():
    f = request.form
    name = f["leave_type_name"].strip()
    code = f["leave_code"].strip().upper()
    if not name or not code:
        flash("Leave type name and code are required.", "error")
        return redirect(url_for("leave_types_admin"))
    try:
        annual_quota = float(f.get("annual_quota") or 0)
    except ValueError:
        annual_quota = 0
    max_order = db.query("SELECT COALESCE(MAX(DisplayOrder),0) AS mo FROM LeaveTypes", one=True)["mo"]
    try:
        db.execute("""INSERT INTO LeaveTypes (LeaveTypeName, LeaveCode, Active, DisplayOrder, AnnualQuota)
                    VALUES (?,?,1,?,?)""", (name, code, max_order + 1, annual_quota))
        flash(f"Leave type '{name}' added.", "success")
    except Exception:
        flash(f"Could not add leave type — '{code}' may already exist.", "error")
    return redirect(url_for("leave_types_admin"))


@bp.route("/attendance/leave-types/<int:leave_type_id>/update", methods=["POST"])
def leave_type_update(leave_type_id):
    try:
        annual_quota = float(request.form.get("annual_quota") or 0)
    except ValueError:
        annual_quota = 0
    lt = db.query("SELECT LeaveTypeName FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,), one=True)
    if lt:
        db.execute("UPDATE LeaveTypes SET AnnualQuota=? WHERE LeaveTypeID=?", (annual_quota, leave_type_id))
        flash(f"Annual quota for '{lt['LeaveTypeName']}' updated to {annual_quota:g} days/year — applies to every employee.", "success")
    return redirect(url_for("leave_types_admin"))


@bp.route("/attendance/leave-types/<int:leave_type_id>/toggle", methods=["POST"])
def leave_type_toggle(leave_type_id):
    lt = db.query("SELECT * FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,), one=True)
    if lt:
        db.execute("UPDATE LeaveTypes SET Active=? WHERE LeaveTypeID=?", (0 if lt["Active"] else 1, leave_type_id))
    return redirect(url_for("leave_types_admin"))


@bp.route("/attendance/leave-types/<int:leave_type_id>/delete", methods=["POST"])
@admin_required
def leave_type_delete(leave_type_id):
    db.execute("DELETE FROM LeaveTypes WHERE LeaveTypeID=?", (leave_type_id,))
    flash("Leave type deleted (its quotas and any recorded leave days against it are removed too).", "success")
    return redirect(url_for("leave_types_admin"))


@bp.route("/attendance/leave-balance")
def leave_balance():
    today = date.today()
    year = int(request.args.get("year", today.year))
    leave_types = get_active_leave_types()
    employees = db.query("SELECT * FROM Employees WHERE Status='Active' ORDER BY EmployeeName")
    quotas = get_shared_leave_quotas()
    rows = []
    for e in employees:
        per_type = []
        for lt in leave_types:
            quota = quotas.get(lt["LeaveTypeID"], 0)
            used = get_leave_used_in_year(e["EmployeeID"], lt["LeaveTypeID"], year)
            per_type.append(dict(leave_type=lt, quota=quota, used=used, balance=max(quota - used, 0)))
        rows.append(dict(employee=e, per_type=per_type))
    return render_template("leave_balance.html", rows=rows, leave_types=leave_types, year=year)
