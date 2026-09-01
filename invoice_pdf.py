"""Generates a GST-compliant tax invoice PDF for a sale, using reportlab
(pure Python, no system dependencies). Called from app.py's
/sales/<id>/invoice.pdf route.

Field coverage follows Rule 46 of the CGST Rules: supplier name/address/
GSTIN, invoice number & date, customer name/address/GSTIN (or state for
unregistered buyers), HSN/SAC per line, taxable value, CGST/SGST/IGST
rates & amounts, place of supply, total invoice value, amount in words,
and a signatory line. It does NOT generate an e-invoice IRN/QR code —
that requires live integration with the GST e-invoice portal (relevant
only once the business crosses the current e-invoicing turnover
threshold), which is outside what a locally generated PDF can produce.
"""
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

styles = getSampleStyleSheet()
NORMAL = ParagraphStyle("normal", parent=styles["Normal"], fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
TITLE = ParagraphStyle("title", parent=styles["Normal"], fontSize=14, leading=16, alignment=TA_CENTER, fontName="Helvetica-Bold")
HEADER_BOLD = ParagraphStyle("header_bold", parent=styles["Normal"], fontSize=10.5, leading=13, fontName="Helvetica-Bold")
LABEL = ParagraphStyle("label", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
RIGHT = ParagraphStyle("right", parent=NORMAL, alignment=TA_RIGHT)


def _p(text, style=NORMAL):
    return Paragraph(str(text) if text is not None else "", style)


def build_invoice_pdf(sale, lines, company, amount_words):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=12 * mm, rightMargin=12 * mm)
    story = []

    story.append(_p("TAX INVOICE", TITLE))
    story.append(Spacer(1, 6))

    # --- Supplier / invoice meta header -------------------------------------------------
    supplier_block = [
        _p(company["CompanyName"] or "Your Company Name", HEADER_BOLD),
        _p(company["Address"] or "", NORMAL),
        _p(f"{company['City'] or ''} {company['State'] or ''} {company['Pincode'] or ''}".strip(), NORMAL),
        _p(f"GSTIN: <b>{company['GSTIN'] or '-'}</b>  |  PAN: {company['PAN'] or '-'}", NORMAL),
        _p(f"Phone: {company['Phone'] or '-'}  |  Email: {company['Email'] or '-'}", NORMAL),
    ]
    meta_block = [
        _p(f"Invoice No: <b>{sale['InvoiceNumber']}</b>", NORMAL),
        _p(f"Invoice Date: <b>{sale['SaleDate']}</b>", NORMAL),
        _p(f"Place of Supply: {sale['PlaceOfSupplyState'] or '-'} ({sale['PlaceOfSupplyStateCode'] or '-'})", NORMAL),
        _p(f"Reverse Charge Applicable: {'Yes' if sale['ReverseCharge'] else 'No'}", NORMAL),
        _p(f"Tax Type: {'IGST (Inter-State)' if sale['IsInterState'] else 'CGST + SGST (Intra-State)'}", NORMAL),
    ]
    header_tbl = Table([[supplier_block, meta_block]], colWidths=[100 * mm, 74 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("LINEAFTER", (0, 0), (0, 0), 0.6, colors.HexColor("#333333")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 4))

    # --- Bill To --------------------------------------------------------------------------
    bill_to = [
        _p("Bill To:", LABEL),
        _p(sale["CustomerName"], HEADER_BOLD),
        _p(sale["Address"] or "", NORMAL),
        _p(f"Phone: {sale['Phone'] or '-'}", NORMAL),
        _p(f"GSTIN: {sale['CustomerGSTIN'] or 'Unregistered'}", NORMAL),
        _p(f"State: {sale['CustomerState'] or '-'} ({sale['CustomerStateCode'] or '-'})", NORMAL),
    ]
    bill_tbl = Table([[bill_to]], colWidths=[174 * mm])
    bill_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(bill_tbl)
    story.append(Spacer(1, 8))

    # --- Line items -------------------------------------------------------------------------
    is_inter = sale["IsInterState"]
    if is_inter:
        head = ["#", "Description", "HSN/SAC", "Qty", "Unit", "Rate", "Taxable Val", "IGST %", "IGST Amt", "Total"]
        col_widths = [7, 36, 19, 12, 12, 16, 20, 15, 16, 21]
    else:
        head = ["#", "Description", "HSN/SAC", "Qty", "Unit", "Rate", "Taxable Val", "CGST%", "CGST", "SGST%", "SGST", "Total"]
        col_widths = [7, 32, 19, 11, 10, 14, 18, 12, 13, 12, 13, 19]
    col_widths = [w * mm for w in col_widths]

    data = [[_p(h, ParagraphStyle("th", parent=NORMAL, fontName="Helvetica-Bold", fontSize=7.5)) for h in head]]
    for i, l in enumerate(lines, start=1):
        line_total = l["TaxableValue"] + l["CGSTAmount"] + l["SGSTAmount"] + l["IGSTAmount"]
        if is_inter:
            row = [str(i), l["ProductName"], l["HSNCode"] or "-", f"{l['Qty']:g}", l["Unit"],
                   f"{l['UnitPrice']:.2f}", f"{l['TaxableValue']:.2f}",
                   f"{l['IGSTRate']:g}%", f"{l['IGSTAmount']:.2f}", f"{line_total:.2f}"]
        else:
            row = [str(i), l["ProductName"], l["HSNCode"] or "-", f"{l['Qty']:g}", l["Unit"],
                   f"{l['UnitPrice']:.2f}", f"{l['TaxableValue']:.2f}",
                   f"{l['CGSTRate']:g}%", f"{l['CGSTAmount']:.2f}", f"{l['SGSTRate']:g}%",
                   f"{l['SGSTAmount']:.2f}", f"{line_total:.2f}"]
        data.append([_p(c, ParagraphStyle("td", parent=NORMAL, fontSize=7.5)) for c in row])

    items_tbl = Table(data, colWidths=col_widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 6))

    # --- Totals -----------------------------------------------------------------------------
    totals_rows = [["Taxable Amount", f"Rs. {sale['TaxableAmount']:.2f}"]]
    if is_inter:
        totals_rows.append(["IGST", f"Rs. {sale['IGSTAmount']:.2f}"])
    else:
        totals_rows.append(["CGST", f"Rs. {sale['CGSTAmount']:.2f}"])
        totals_rows.append(["SGST", f"Rs. {sale['SGSTAmount']:.2f}"])
    totals_rows.append(["Round Off", f"Rs. {sale['RoundOff']:.2f}"])
    totals_rows.append(["Total Invoice Value", f"Rs. {sale['TotalAmount']:.2f}"])

    totals_data = [[_p(a, NORMAL), _p(b, RIGHT)] for a, b in totals_rows]
    totals_tbl = Table(totals_data, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#333333")),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 6))

    story.append(_p(f"<b>Amount in Words:</b> {amount_words}", NORMAL))
    story.append(Spacer(1, 10))

    # --- Bank + terms + signature ------------------------------------------------------------
    bank_lines = []
    if company["BankName"]:
        bank_lines.append(_p("<b>Bank Details for Payment</b>", LABEL))
        bank_lines.append(_p(f"Bank: {company['BankName']}, {company['BankBranch'] or ''}", SMALL))
        bank_lines.append(_p(f"A/c Name: {company['BankAccountName'] or '-'}", SMALL))
        bank_lines.append(_p(f"A/c No: {company['BankAccountNumber'] or '-'}   IFSC: {company['BankIFSC'] or '-'}", SMALL))
    else:
        bank_lines.append(_p("", SMALL))

    sign_block = [
        _p(f"For {company['CompanyName'] or 'Your Company Name'}", NORMAL),
        Spacer(1, 22),
        _p("Authorised Signatory", LABEL),
    ]
    bottom_tbl = Table([[bank_lines, sign_block]], colWidths=[104 * mm, 70 * mm])
    bottom_tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(bottom_tbl)
    story.append(Spacer(1, 8))

    story.append(_p(f"<b>Terms & Conditions:</b> {company['InvoiceTerms'] or ''}", SMALL))
    story.append(Spacer(1, 4))
    story.append(_p("This is a computer generated invoice.", SMALL))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
