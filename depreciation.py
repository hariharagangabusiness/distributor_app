"""Vehicle depreciation under the Income Tax Act, 1961 (Section 32),
for the "Motor Vehicles (other than those used in a business of running
them on hire)" block - the correct block for a distributor's own
delivery/fleet vehicles.

Why this treatment, and not something simpler:

- A vehicle is a CAPITAL asset. Its full purchase price is never a P&L
  expense in the year of purchase - that would both misstate profit and
  isn't what the Income Tax Act allows. Instead the asset is capitalised
  and written down over its useful life via depreciation, which IS the
  deductible expense each year.
- The Income Tax Act uses the Written Down Value (WDV) method with a
  15% per annum rate for this block of assets (Appendix I, Income Tax
  Rules - the "general" motor vehicle rate; the 30% rate only applies to
  vehicles actually run as a hire/taxi business, which a distributor's
  own fleet is not).
- The "180-day rule": if an asset is purchased and put to use for less
  than 180 days in the financial year of purchase, only HALF the normal
  rate (7.5%) is allowed for that first year - full 15% from the next
  year onward.
- Income tax technically depreciates a whole BLOCK of assets (all motor
  vehicles together), not each vehicle separately, and only nets out an
  individual asset when it's sold. Since this app doesn't yet track
  vehicle disposals, computing each vehicle's own WDV schedule with this
  same rate and simply summing them across vehicles gives the identical
  answer as the block method would - so per-vehicle computation here is
  not an approximation, it's mathematically the same thing, for as long
  as no vehicle has been sold. (If/when a "Sold" vehicle workflow with a
  sale price and date is added, this will need updating to properly
  remove that vehicle's WDV from the block and compute any short-term
  capital gain/loss on the block - a materially different calculation.)
- Routine repairs/servicing (VehicleMaintenance.Cost) are treated
  separately as REVENUE expenditure - fully deductible in the year
  incurred under Section 37(1), not capitalised. That's handled in
  app.py's P&L route directly, not here.

None of this changes how the Books of Accounts under the Companies Act
would be kept (which can use a different useful-life/SLM basis under
Schedule II) - for a proprietorship/partnership distributor business
without statutory audit obligations under the Companies Act, aligning
book depreciation with the Income Tax WDV schedule is the standard,
simplest, and most audit-safe approach (one number, no separate
book-vs-tax reconciliation to maintain).
"""
from datetime import date

VEHICLE_DEPRECIATION_RATE = 0.15   # Income Tax Act, motor vehicles (general block)
HALF_YEAR_THRESHOLD_DAYS = 180     # used < 180 days in the FY of purchase -> half rate


def indian_fy_bounds(d):
    """(fy_start, fy_end) as date objects for the Indian financial year
    (1 April - 31 March) containing date `d`."""
    if d.month >= 4:
        return date(d.year, 4, 1), date(d.year + 1, 3, 31)
    return date(d.year - 1, 4, 1), date(d.year, 3, 31)


def vehicle_depreciation_schedule(purchase_date, purchase_price, upto_date):
    """Year-by-year WDV depreciation schedule for one vehicle, from its
    financial year of purchase through the financial year containing
    `upto_date`. Returns a list of dicts, one per FY, each with the
    opening WDV, the rate actually applied, the FY's depreciation amount,
    the closing WDV, and the exact date range within that FY the vehicle
    was actually owned/depreciable (`use_start`..`fy_end`) - this range is
    the correct base for apportioning that FY's depreciation across any
    shorter reporting period, since the FY's depreciation itself was
    computed on however many of those days fall in the 180-day test.
    """
    if not purchase_date or not purchase_price or purchase_price <= 0:
        return []
    if upto_date < purchase_date:
        return []

    schedule = []
    fy_start, fy_end = indian_fy_bounds(purchase_date)
    upto_fy_end = indian_fy_bounds(upto_date)[1]
    opening = round(float(purchase_price), 2)

    while fy_start <= upto_fy_end:
        use_start = max(fy_start, purchase_date)
        days_used = (fy_end - use_start).days + 1
        rate = VEHICLE_DEPRECIATION_RATE if days_used >= HALF_YEAR_THRESHOLD_DAYS else VEHICLE_DEPRECIATION_RATE / 2
        dep = round(min(opening * rate, opening), 2)
        closing = round(opening - dep, 2)
        schedule.append({
            "fy_start": fy_start, "fy_end": fy_end, "use_start": use_start,
            "opening_wdv": opening, "rate": rate, "depreciation": dep, "closing_wdv": closing,
            "days_used": days_used,
        })
        opening = closing
        fy_start, fy_end = date(fy_end.year, 4, 1), date(fy_end.year + 1, 3, 31)

    return schedule


def vehicle_depreciation_for_range(purchase_date, purchase_price, date_from, date_to):
    """Depreciation attributable to [date_from, date_to] for one vehicle -
    apportions each overlapping FY's WDV depreciation by the number of
    days of that FY (from `use_start`) that fall inside the requested
    range, out of the days that FY's depreciation was itself based on.
    A report run for exactly one full financial year reproduces the
    Income Tax figure for that year exactly; any other range is a
    day-prorated estimate for interim/MIS reporting.
    """
    if date_from > date_to:
        return 0.0
    schedule = vehicle_depreciation_schedule(purchase_date, purchase_price, date_to)
    total = 0.0
    for row in schedule:
        overlap_start = max(date_from, row["use_start"])
        overlap_end = min(date_to, row["fy_end"])
        if overlap_start > overlap_end or row["days_used"] <= 0:
            continue
        overlap_days = (overlap_end - overlap_start).days + 1
        total += row["depreciation"] * (overlap_days / row["days_used"])
    return round(total, 2)


def current_wdv(purchase_date, purchase_price, as_of_date):
    """The vehicle's Written Down Value as of `as_of_date` (i.e., after
    all full financial years up to and including the one containing
    as_of_date have been depreciated) - useful for a fixed-asset register
    view, not currently rendered anywhere but kept here for reuse."""
    schedule = vehicle_depreciation_schedule(purchase_date, purchase_price, as_of_date)
    if not schedule:
        return round(float(purchase_price or 0), 2)
    return schedule[-1]["closing_wdv"]
