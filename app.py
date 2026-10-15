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
