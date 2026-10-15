import io
import math
import re
from datetime import date

import altair as alt
import pdfplumber
import pandas as pd
import streamlit as st
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

st.set_page_config(page_title="Blood Age Explorer", page_icon="🩸", layout="wide")

# ------------------------------------------------------------------
# 1. THE MARKERS
# For each marker: its PhenoAge coefficient, and how to show it in
# US or SI lab units. Each unit tuple is:
#   (unit label, slider min, slider max, reference value, step,
#    factor that converts this unit into the unit the formula uses)
# ------------------------------------------------------------------
MARKERS = [
    dict(key="albumin", name="Albumin", coef=-0.0336,
         us=("g/dL", 2.5, 5.5, 4.5, 0.1, 10.0),
         si=("g/L", 25.0, 55.0, 45.0, 0.5, 1.0)),
    dict(key="creatinine", name="Creatinine", coef=0.0095,
         us=("mg/dL", 0.30, 5.00, 0.90, 0.05, 88.4),
         si=("µmol/L", 25.0, 440.0, 80.0, 1.0, 1.0)),
    dict(key="glucose", name="Glucose (fasting)", coef=0.1953,
         us=("mg/dL", 50.0, 300.0, 90.0, 1.0, 1 / 18.016),
         si=("mmol/L", 2.8, 16.7, 5.0, 0.1, 1.0)),
    dict(key="crp", name="C-reactive protein (CRP)", coef=0.0954,
         us=("mg/L", 0.1, 100.0, 1.0, 0.1, 0.1),
         si=("mg/L", 0.1, 100.0, 1.0, 0.1, 0.1)),
    dict(key="lymph", name="Lymphocytes", coef=-0.0120,
         us=("%", 10.0, 50.0, 30.0, 0.5, 1.0),
         si=("%", 10.0, 50.0, 30.0, 0.5, 1.0)),
    dict(key="mcv", name="Mean cell volume (MCV)", coef=0.0268,
         us=("fL", 75.0, 105.0, 90.0, 0.5, 1.0),
         si=("fL", 75.0, 105.0, 90.0, 0.5, 1.0)),
    dict(key="rdw", name="Red cell distribution width (RDW)", coef=0.3306,
         us=("%", 11.0, 18.0, 13.0, 0.1, 1.0),
         si=("%", 11.0, 18.0, 13.0, 0.1, 1.0)),
    dict(key="alp", name="Alkaline phosphatase", coef=0.00188,
         us=("U/L", 20.0, 300.0, 70.0, 1.0, 1.0),
         si=("U/L", 20.0, 300.0, 70.0, 1.0, 1.0)),
    dict(key="wbc", name="White blood cells (WBC)", coef=0.0554,
         us=("×10³/µL", 1.0, 20.0, 6.0, 0.1, 1.0),
         si=("×10⁹/L", 1.0, 20.0, 6.0, 0.1, 1.0)),
]


# ------------------------------------------------------------------
# 2. THE ENGINE: Levine's Phenotypic Age formula
# Expects values in the formula's own units (g/L, µmol/L, mmol/L,
# CRP in mg/dL, and so on).
# ------------------------------------------------------------------
def phenoage(age, values):
    xb = -19.9067 + 0.0804 * age
    for m in MARKERS:
        x = values[m["key"]]
        if m["key"] == "crp":
            x = math.log(x)  # CRP enters the formula on a log scale
        xb += m["coef"] * x

    gamma = 0.0076927
    mortality = 1 - math.exp(-math.exp(xb) * (math.exp(120 * gamma) - 1) / gamma)
    return 141.50225 + math.log(-0.00553 * math.log(1 - mortality)) / 0.09165


# ------------------------------------------------------------------
# 3. THE READER: pull the nine markers out of a text-based PDF
# Pattern matching only: the file never leaves this server.
# For each marker we look for lines that mention it (and skip look-alikes
# such as "Albumin/Globulin Ratio"), take the first number after the name,
# then work out the unit from the line, or from the size of the number.
# ------------------------------------------------------------------
PATTERNS = {
    "albumin":    ([r"\balbumin\b"], [r"globulin", r"ratio", r"micro", r"urine", r"prealbumin", r"\ba/g\b"]),
    "creatinine": ([r"\bcreatinine\b"], [r"ratio", r"clearance", r"urine", r"egfr", r"kinase", r"/\s*creat"]),
    "glucose":    ([r"\bglucose\b"], [r"urine", r"tolerance", r"\bua\b", r"negative", r"trace", r"\d\+"]),
    "crp":        ([r"c[\s-]?reactive\s+protein", r"\bhs[\s-]?crp\b", r"\bcrp\b"], []),
    "lymph":      ([r"\blymphocytes?\b", r"\blymphs?\b", r"\blym\b"], [r"absolute", r"\babs\b", r"atypical", r"#", r"reactive"]),
    "mcv":        ([r"\bmcv\b", r"mean\s+corpuscular\s+volume", r"mean\s+cell\s+volume"], []),
    "rdw":        ([r"\brdw\b", r"red\s+cell\s+distribution\s+width"], [r"rdw[\s-]?sd", r"\bsd\b"]),
    "alp":        ([r"alkaline\s+phosphatase", r"\balk\.?\s*phos", r"\balp\b"], [r"bone", r"isoenzyme"]),
    "wbc":        ([r"\bwbc\b", r"white\s+blood\s+cell", r"\bleukocytes?\b"], [r"urine", r"esterase", r"\bua\b", r"none seen"]),
}
NUMBER = re.compile(r"(<|>)?\s*(\d+(?:\.\d+)?)")
# Lines that are never a result: section headers with dates, urinalysis microscopy
SKIP_LINES = [r"collected:", r"received:", r"reported:", r"/\s*hpf", r"/\s*lpf"]


def first_result_number(text):
    """First number in text that isn't a footnote code ("01") or part of a date or time."""
    for n in NUMBER.finditer(text):
        start, end = n.start(2), n.end(2)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if re.fullmatch(r"0\d+", n.group(2)) or (before and before in "/:") or (after and after in "/:"):
            continue
        return n
    return None


def to_model_units(key, value, line):
    """Return (value in the formula's units, unit we read, note)."""
    low = line.lower()
    if key == "albumin":
        if "g/dl" in low:
            return value * 10, "g/dL", ""
        if re.search(r"g/l\b", low):
            return value, "g/L", ""
        return (value * 10, "g/dL", "unit guessed") if value < 10 else (value, "g/L", "unit guessed")
    if key == "creatinine":
        if "mg/dl" in low:
            return value * 88.4, "mg/dL", ""
        if re.search(r"[uµμ]mol", low):
            return value, "µmol/L", ""
        return (value * 88.4, "mg/dL", "unit guessed") if value < 20 else (value, "µmol/L", "unit guessed")
    if key == "glucose":
        if "mg/dl" in low:
            return value / 18.016, "mg/dL", ""
        if "mmol" in low:
            return value, "mmol/L", ""
        return (value / 18.016, "mg/dL", "unit guessed") if value > 30 else (value, "mmol/L", "unit guessed")
    if key == "crp":
        if "mg/dl" in low:
            return value, "mg/dL", ""
        if re.search(r"mg/l\b", low):
            return value * 0.1, "mg/L", ""
        return value * 0.1, "mg/L", "unit guessed"
    if key == "wbc" and value > 1000:  # reported as cells/µL
        return value / 1000, "cells/µL", ""
    if key == "lymph" and value > 70:
        return value, "%", "check: may not be a percentage"
    return value, "", ""


def read_report(file_bytes):
    """Return (dict of found markers, age or None, number of text characters)."""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    found = {}
    for key, (names, excludes) in PATTERNS.items():
        candidates = []
        for ln in lines:
            low = ln.lower()
            if any(re.search(x, low) for x in excludes + SKIP_LINES):
                continue
            hit = None
            for name in names:
                hit = re.search(name, low)
                if hit:
                    break
            if not hit:
                continue
            number = first_result_number(ln[hit.end():])
            if number:
                candidates.append((ln, number))
        if not candidates:
            continue
        if key == "lymph":  # prefer the percentage line over the absolute count
            candidates.sort(key=lambda c: "%" not in c[0])
        line, number = candidates[0]
        raw = float(number.group(2))
        model_value, unit, note = to_model_units(key, raw, line)
        if number.group(1):
            note = (note + "; " if note else "") + f"reported as {number.group(1)}{raw:g}"
        found[key] = dict(model=model_value, raw=raw, unit=unit, note=note, line=line)

    age = None
    m = re.search(r"\bage(?:\s*/\s*sex)?\s*[:\-]?\s*(\d{1,3})\b", text, re.I)
    if m and 18 <= int(m.group(1)) <= 100:
        age = int(m.group(1))
    return found, age, len(text.strip())


def set_sliders(model_values):
    """Write values (in formula units) into both the US and SI sliders."""
    for mk in MARKERS:
        for sys in ("us", "si"):
            _, lo, hi, _, _, factor = mk[sys]
            shown = model_values[mk["key"]] / factor
            st.session_state[f"{mk['key']}_{sys}"] = float(min(max(round(shown, 2), lo), hi))


REFERENCE_MODEL = {mk["key"]: mk["si"][3] * mk["si"][5] for mk in MARKERS}


def reset_all():
    set_sliders(REFERENCE_MODEL)
    st.session_state["age"] = 45
    st.session_state.pop("extraction", None)
    st.session_state.pop("last_file", None)


# Start every slider at its reference value (only on the first run)
if "albumin_us" not in st.session_state:
    set_sliders(REFERENCE_MODEL)
if "age" not in st.session_state:
    st.session_state["age"] = 45


# ------------------------------------------------------------------
# 4. THE PDF REPORT: a one-click summary people can download and keep
# Built with ReportLab. Nothing is stored; the PDF is made fresh in memory.
# ------------------------------------------------------------------
def pdf_unit(unit):
    """Built-in PDF fonts lack superscript digits, so use markup instead."""
    unit = unit.replace("µ", "u").replace("μ", "u")  # the micro sign is missing from the built-in font
    return unit.replace("³", "<super>3</super>").replace("⁹", "<super>9</super>")


def build_pdf(age, bio, gap, message, marker_rows, chart_rows):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title="PhenoAge report", author="Blood Age Explorer")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#555555"))
    cell = ParagraphStyle("cell", parent=body, fontSize=9, leading=11)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4)
    story = []

    story.append(Paragraph("Blood Age Explorer: PhenoAge report", ss["Title"]))
    story.append(Paragraph(f"Generated {date.today():%B %d, %Y} &nbsp;|&nbsp; "
                           "Estimate based on Levine's Phenotypic Age formula", small))
    story.append(Spacer(1, 10))

    # --- headline numbers
    headline = Table(
        [["PhenoAge estimate", "Chronological age", "Difference"],
         [f"{bio:.1f} years", f"{age} years", f"{gap:+.1f} years"]],
        colWidths=[2.2 * inch] * 3,
    )
    headline.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#555555")),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 16),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f7f8")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(headline)
    story.append(Spacer(1, 6))
    story.append(Paragraph(message, body))

    # --- marker table
    story.append(Paragraph("Your markers", h2))
    data = [[Paragraph("<b>Marker</b>", cell), Paragraph("<b>Your value</b>", cell),
             Paragraph("<b>Reference</b>", cell), Paragraph("<b>Effect (years)</b>", cell)]]
    for name, value, ref, unit, effect in marker_rows:
        u = pdf_unit(unit)
        data.append([Paragraph(name, cell), Paragraph(f"{value:g} {u}", cell),
                     Paragraph(f"{ref:g} {u}", cell), Paragraph(f"{effect:+.1f}", cell)])
    t = Table(data, colWidths=[2.4 * inch, 1.5 * inch, 1.5 * inch, 1.2 * inch], repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor("#333333")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7f8")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Paragraph("Reference values are typical healthy values used as the comparison point, not clinical targets.", small))

    # --- bar chart
    story.append(Paragraph("What's moving your estimate", h2))
    width, row_h, label_w = 6.6 * inch, 16, 2.6 * inch
    height = row_h * len(chart_rows) + 20
    d = Drawing(width, height)
    biggest = max(1.0, max(abs(v) for _, v, _ in chart_rows))
    plot_w = width - label_w - 40
    zero_x = label_w + plot_w / 2
    scale = (plot_w / 2) / biggest
    palette = {"Adds years": "#c0392b", "Takes years off": "#1f8a70", "Baseline": "#9aa0a6"}
    for i, (name, years, kind) in enumerate(chart_rows):
        y = height - 14 - (i + 1) * row_h + 4
        d.add(String(label_w - 6, y + 3, name, fontName="Helvetica", fontSize=8, textAnchor="end"))
        x0 = zero_x if years >= 0 else zero_x + years * scale
        d.add(Rect(x0, y, abs(years) * scale, row_h - 5,
                   fillColor=colors.HexColor(palette[kind]), strokeColor=None))
        tx = zero_x + years * scale + (4 if years >= 0 else -4)
        d.add(String(tx, y + 3, f"{years:+.1f}", fontName="Helvetica", fontSize=7.5,
                     textAnchor="start" if years >= 0 else "end"))
    d.add(Line(zero_x, 6, zero_x, height - 10, strokeColor=colors.HexColor("#666666"),
