"""Shared number formatting for templates, PDFs and exports."""


def indian_number_format(value, decimals=2):
    """Format a number Indian-style: groups of 2 after the first 3 digits from the
    right (e.g. 862000 -> 8,62,000.00), rather than the Western groups-of-3 style
    (862,000.00). Used everywhere a rupee amount or a plain count is shown."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0.0
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
