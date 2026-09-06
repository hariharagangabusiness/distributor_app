"""Generates a payslip PDF for one employee/month's SalaryPayments record,
using reportlab (pure Python, no system dependencies) - same approach as
invoice_pdf.py. Called from app.py's /salary/<id>/payslip.pdf route.

Shows: a properly centered company header (name + full address on their
own lines, not squeezed together), employee details (name, designation,
employee ID, bank account), the pay period, days worked / leave days
taken (from the Leave & Attendance tab when recorded), an earnings/
deductions breakdown (gross salary, LOP days & the resulting basic,
bonus, deductions, advance deducted), net payable, amount in words, and
a payment status/date line. All money and day figures use Indian-style
digit grouping (e.g. 8,62,000.00), matching the rest of the app.
"""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

styles = getSampleStyleSheet()
NORMAL = ParagraphStyle("normal", parent=styles["Normal"], fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
SMALL_CENTER = ParagraphStyle("small_center", parent=SMALL, alignment=TA_CENTER)
TITLE = ParagraphStyle("title", parent=styles["Normal"], fontSize=15, leading=18, alignment=TA_CENTER, fontName="Helvetica-Bold")
SUBTITLE = ParagraphStyle("subtitle", parent=styles["Normal"], fontSize=10, leading=13, alignment=TA_CENTER, fontName="Helvetica-Bold", textColor=colors.HexColor("#555555"))
HEADER_BOLD = ParagraphStyle("header_bold", parent=styles["Normal"], fontSize=10.5, leading=13, fontName="Helvetica-Bold")
LABEL = ParagraphStyle("label", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
RIGHT = ParagraphStyle("right", parent=NORMAL, alignment=TA_RIGHT)


def _indian_number_format(value, decimals=2):
    """Indian-style grouped number, e.g. 862000 -> 8,62,000.00. Mirrors
    app.py's indian_number_format() template filter so PDF and on-screen
    figures always match."""
    value = value or 0
    negative = value < 0
    value = abs(value)
    if decimals > 0:
        whole_str, dec_str = f"{value:.{decimals}f}".split(".")
    else:
        whole_str, dec_str = f"{value:.0f}", None
    if len(whole_str) > 3:
        last_three = whole_str[-3:]
        rest = whole_str[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        whole_str = ",".join(groups) + "," + last_three
    result = whole_str + (f".{dec_str}" if dec_str is not None else "")
    return ("-" if negative else "") + result


def _inr(value, decimals=2):
    return "Rs. " + _indian_number_format(value, decimals)


def _days(value):
    v = value or 0
    return f"{v:g}"


def _p(text, style=NORMAL):
    return Paragraph(str(text) if text is not None else "", style)


def build_payslip_pdf(payment, employee, company, month_name, amount_words,
                       days_in_month=None, days_worked=None, leave_days_total=0,
                       leave_details=None, attendance_recorded=False):
    leave_details = leave_details or []
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm)
    story = []

    # --- Company header: name centered on its own line, full address
    # (street, city/state/pincode) on its own line beneath it, each
    # centered independently so a long address never crowds the name. ---
    story.append(_p(company["CompanyName"] or "Your Company Name", TITLE))
    address_line1 = (company["Address"] or "").strip()
    city_state_pin = " ".join(x for x in [company["City"], company["State"], company["Pincode"]] if x).strip()
    if address_line1:
        story.append(_p(address_line1, SMALL_CENTER))
    if city_state_pin:
        story.append(_p(city_state_pin, SMALL_CENTER))
    contact_bits = []
    if company["Phone"]:
        contact_bits.append(f"Phone: {company['Phone']}")
    if company["Email"]:
        contact_bits.append(f"Email: {company['Email']}")
    if contact_bits:
        story.append(_p("  |  ".join(contact_bits), SMALL_CENTER))
    story.append(Spacer(1, 6))
    story.append(_p(f"PAYSLIP FOR {month_name.upper()} {payment['SalaryYear']}", SUBTITLE))
    story.append(Spacer(1, 10))

    # --- Employee details -----------------------------------------------------------------
    emp_block = [
        _p("Employee Name:", LABEL), _p(employee["EmployeeName"], HEADER_BOLD),
        _p("Designation:", LABEL), _p(employee["Designation"] or "-", NORMAL),
        _p("Employee ID:", LABEL), _p(f"EMP-{employee['EmployeeID']:04d}", NORMAL),
    ]
    pay_block = [
        _p("Pay Period:", LABEL), _p(f"{month_name} {payment['SalaryYear']}", NORMAL),
        _p("Bank Account:", LABEL), _p(employee["BankAccount"] or "-", NORMAL),
        _p("Payment Status:", LABEL),
        _p(f"{payment['Status']}" + (f" on {payment['PaymentDate']} ({payment['PaymentMode']})" if payment["Status"] == "Paid" and payment["PaymentDate"] else ""), NORMAL),
    ]
    detail_tbl = Table([[emp_block, pay_block]], colWidths=[91 * mm, 91 * mm])
    detail_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("LINEAFTER", (0, 0), (0, 0), 0.6, colors.HexColor("#333333")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(detail_tbl)
    story.append(Spacer(1, 6))

    # --- Attendance summary: days worked / leaves taken -------------------------------------
    th = ParagraphStyle("th2", parent=NORMAL, fontName="Helvetica-Bold", fontSize=8.5)
    att_row = [
        _p("Days in Month", th), _p("Days Worked", th), _p("Leave Days Taken", th), _p("Loss of Pay Days", th),
    ]
    if days_in_month is None:
        days_in_month = 0
    if days_worked is None:
        days_worked = 0
    att_vals = [_p(_days(days_in_month)), _p(_days(days_worked)), _p(_days(leave_days_total)), _p(_days(payment["LOPDays"]))]
    att_tbl = Table([att_row, att_vals], colWidths=[45.5 * mm] * 4)
    att_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f0")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(att_tbl)
    if leave_details:
        leave_bits = ", ".join(f"{d['LeaveTypeName']}: {_days(d['DaysTaken'])}" for d in leave_details)
        story.append(Spacer(1, 3))
        story.append(_p(f"Leave breakup: {leave_bits}", SMALL))
    elif not attendance_recorded:
        story.append(Spacer(1, 3))
        story.append(_p("Attendance not recorded for this month — days worked shown is derived from payable days.", SMALL))
    story.append(Spacer(1, 8))

    # --- Earnings / Deductions breakdown ---------------------------------------------------
    rows = [[_p("Description", th), _p("Amount", th)]]
    rows.append([_p("Gross Salary (Monthly)"), _p(_inr(payment["GrossSalary"]), RIGHT)])
    if payment["LOPDays"]:
        rows.append([_p(f"Less: Loss of Pay ({_days(payment['LOPDays'])} day(s))"),
                     _p(f"- {_inr(payment['GrossSalary'] - payment['BasicAmount'])}", RIGHT)])
    rows.append([_p("Basic Pay (after LOP adjustment)", ParagraphStyle("b", parent=NORMAL, fontName="Helvetica-Bold")),
                 _p(_inr(payment["BasicAmount"]), ParagraphStyle("b", parent=RIGHT, fontName="Helvetica-Bold"))])
    if payment["Bonus"]:
        rows.append([_p("Add: Bonus"), _p(_inr(payment["Bonus"]), RIGHT)])
    if payment["Deductions"]:
        rows.append([_p("Less: Other Deductions"), _p(f"- {_inr(payment['Deductions'])}", RIGHT)])
    if payment["AdvanceDeducted"]:
        rows.append([_p("Less: Advance Deducted"), _p(f"- {_inr(payment['AdvanceDeducted'])}", RIGHT)])
    rows.append([_p("Net Payable", ParagraphStyle("nb", parent=NORMAL, fontName="Helvetica-Bold", fontSize=10)),
                 _p(_inr(payment["NetPayable"]), ParagraphStyle("nbr", parent=RIGHT, fontName="Helvetica-Bold", fontSize=10))])

    pay_tbl = Table(rows, colWidths=[128 * mm, 54 * mm])
    pay_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f0")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5f5f5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(pay_tbl)
    story.append(Spacer(1, 8))

    story.append(_p(f"<b>Net Amount in Words:</b> {amount_words}", NORMAL))
    story.append(Spacer(1, 24))

    sign_tbl = Table([[
        [_p(f"For {company['CompanyName'] or 'Your Company Name'}", NORMAL), Spacer(1, 22), _p("Authorised Signatory", LABEL)],
        [_p("Employee Signature", LABEL)],
    ]], colWidths=[91 * mm, 91 * mm])
    sign_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    story.append(sign_tbl)
    story.append(Spacer(1, 10))

    story.append(_p("This is a computer generated payslip and does not require a physical signature.", SMALL))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
