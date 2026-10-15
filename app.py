import io
import math
import re

import altair as alt
import pdfplumber
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Biological Age Explorer", page_icon="🩸", layout="wide")

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
# 4. THE INTERFACE: header
# ------------------------------------------------------------------
st.title("Biological Age Explorer")
st.markdown(
    "Estimate your biological age from nine routine blood tests, "
    "using the **PhenoAge** formula developed by Morgan Levine and colleagues."
)

# ------------------------------------------------------------------
# 5. THE UPLOADER: fill the sliders from a lab report
# ------------------------------------------------------------------
uploaded = st.file_uploader(
    "Drop a lab report here to fill in the sliders (text-based PDF)",
    type="pdf",
    help="Read on the server with pattern matching. The file is not saved and is not sent anywhere else.",
)
if uploaded is not None and st.session_state.get("last_file") != uploaded.file_id:
    st.session_state["last_file"] = uploaded.file_id
    try:
        found, report_age, n_chars = read_report(uploaded.getvalue())
    except Exception:
        found, report_age, n_chars = {}, None, -1
    st.session_state["extraction"] = dict(found=found, age=report_age, n_chars=n_chars)
    model_values = dict(REFERENCE_MODEL)
    model_values.update({k: v["model"] for k, v in found.items()})
    set_sliders(model_values)
    if report_age:
        st.session_state["age"] = report_age

extraction = st.session_state.get("extraction")
if extraction:
    found = extraction["found"]
    if extraction["n_chars"] == -1:
        st.error("Couldn't open that PDF. Please enter your values with the sliders.")
    elif extraction["n_chars"] < 50:
        st.error("This PDF has no readable text, so it's probably a scan or photo. "
                 "This reader only handles text-based PDFs. Please enter your values with the sliders.")
    else:
        missing = [mk["name"] for mk in MARKERS if mk["key"] not in found]
        if missing:
            st.warning(f"Found {len(found)} of 9 markers. Not found, so left at reference values: "
                       + ", ".join(missing) + ". Please set these by hand.")
        else:
            st.success("Found all 9 markers.")
        with st.expander("Check what was read from your report", expanded=True):
            st.caption("Pattern matching can misread a report. Compare these with your PDF and fix any slider that's off.")
            table = []
            for mk in MARKERS:
                f = found.get(mk["key"])
                table.append({
                    "Marker": mk["name"],
                    "Value read": f"{f['raw']:g}" if f else "—",
                    "Unit": (f["unit"] or mk["si"][0]) if f else "",
                    "Note": f["note"] if f else "not found",
                    "From this line": f["line"][:80] if f else "",
                })
            st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
            if extraction["age"]:
                st.caption(f"Age read from report: {extraction['age']}")
            else:
                st.caption("No age found on the report. Set your age in the sidebar.")


# ------------------------------------------------------------------
# 6. THE INTERFACE: sidebar inputs
# ------------------------------------------------------------------
st.sidebar.header("Your numbers")
units = st.sidebar.radio("Lab units", ["US", "SI"], horizontal=True)
system = "us" if units == "US" else "si"

age = st.sidebar.slider("Chronological age (years)", 18, 100, key="age")

st.sidebar.caption("Sliders start at typical healthy values. Upload a report or move them to match your labs.")
st.sidebar.button("Reset to reference values", on_click=reset_all)

values = {}      # in the formula's units
reference = {}   # reference values, in the formula's units
for m in MARKERS:
    unit, lo, hi, ref, step, factor = m[system]
    shown = st.sidebar.slider(f"{m['name']} ({unit})", lo, hi, step=step, key=f"{m['key']}_{system}")
    values[m["key"]] = shown * factor
    reference[m["key"]] = ref * factor

# ------------------------------------------------------------------
# 7. THE RESULT
# ------------------------------------------------------------------
bio = phenoage(age, values)
gap = bio - age

c1, c2, c3 = st.columns(3)
c1.metric("PhenoAge estimate", f"{bio:.1f} years")
c2.metric("Chronological age", f"{age} years")
c3.metric("Difference", f"{gap:+.1f} years", delta=f"{gap:+.1f}", delta_color="inverse")

if gap <= -2:
    st.success("Your blood profile resembles that of people younger than you in the reference population.")
elif gap >= 2:
    st.warning("Your blood profile resembles that of people older than you in the reference population.")
else:
    st.info("Your blood profile is close to what is typical for your age in the reference population.")

# ------------------------------------------------------------------
# 8. WHAT'S MOVING YOUR ESTIMATE
# A neat property of the PhenoAge formula: once you work through the
# algebra, the final number is a straight-line function of the
# weighted sum (CRP on a log scale). So each marker's effect, in years,
# is simply  coefficient × (your value − reference value) / 0.09165.
# These effects add up exactly. One extra "baseline" bar holds the gap
# a person with every marker at its reference value would have at your age.
# ------------------------------------------------------------------
st.subheader("What's moving your estimate")

def scaled(key, x):
    return math.log(x) if key == "crp" else x

YEARS_PER_UNIT = 1 / 0.09165

baseline = phenoage(age, reference) - age
rows = [{
    "Factor": "Baseline (reference profile at your age)",
    "Years": baseline,
    "Type": "Baseline",
}]
for m in MARKERS:
    k = m["key"]
    effect = m["coef"] * (scaled(k, values[k]) - scaled(k, reference[k])) * YEARS_PER_UNIT
    rows.append({
        "Factor": m["name"],
        "Years": effect,
        "Type": "Adds years" if effect > 0 else "Takes years off",
    })

df = pd.DataFrame(rows)
df["Size"] = df["Years"].abs()
df = df.sort_values("Size", ascending=False)
df["Label"] = df["Years"].map(lambda v: f"{v:+.1f}")
order = df["Factor"].tolist()

bars = (
    alt.Chart(df)
    .mark_bar()
    .encode(
        x=alt.X("Years:Q", title="Years added (+) or taken off (−)"),
        y=alt.Y("Factor:N", sort=order, title=None),
        color=alt.Color(
            "Type:N",
            scale=alt.Scale(
                domain=["Adds years", "Takes years off", "Baseline"],
                range=["#c0392b", "#1f8a70", "#9aa0a6"],
            ),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=["Factor", alt.Tooltip("Years:Q", format="+.2f")],
    )
)
labels = (
    alt.Chart(df)
    .mark_text(align="left", dx=4)
    .encode(x=alt.X("Years:Q"), y=alt.Y("Factor:N", sort=order), text="Label:N")
)
zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#666").encode(x="x:Q")

st.altair_chart((bars + labels + zero).properties(height=360), use_container_width=True)
st.caption(
    f"These bars add up exactly to your difference of {gap:+.1f} years. "
    "Each marker bar compares your value with a typical healthy reference value (the slider's starting point). "
    "The grey baseline is the difference a person with all reference values would have at your age. "
    "It shifts with age because the formula counts each calendar year as slightly less than one year."
)

# ------------------------------------------------------------------
# 9. ABOUT, LIMITS, AND REFERENCES
# ------------------------------------------------------------------
with st.expander("What is PhenoAge?", expanded=False):
    st.markdown(
        """
PhenoAge (also called Phenotypic Age) combines your chronological age with nine common blood markers
into a single number, measured in years.

Levine and colleagues built it from **NHANES III**, a large US health survey (1988–1994). Starting from
42 lab measures, they kept the combination that best predicted death over the following years. The result
is the age at which an average person in that population would carry the same estimated mortality risk.

In an independent sample (**NHANES IV**, 1999–2010), PhenoAge tracked mortality and chronic disease
across diverse groups, and people with more diseases tended to have an older PhenoAge.

**How to read your number:** a PhenoAge above your actual age means your blood profile looks like that
of older people in the reference population. Below means younger.
        """
    )

with st.expander("Limits worth knowing", expanded=False):
    st.markdown(
        """
- **It's an estimate, not a diagnosis.** It describes population-level risk, not what will happen to you.
- **One blood draw is noisy.** A cold, a hard workout, or poor sleep can raise CRP or white cells for days.
- **It was built on US survey data.** Calibration may differ in other populations.
- **The "Difference" here is a simple subtraction.** Research papers usually report *PhenoAge acceleration*,
  which adjusts for age statistically.
- **This is not medical advice.** Talk with a clinician about any lab result that concerns you.
        """
    )

st.subheader("References")
st.markdown(
    """
1. Levine ME, Lu AT, Quach A, et al. An epigenetic biomarker of aging for lifespan and healthspan.
   *Aging (Albany NY)*. 2018;10(4):573–591. [doi:10.18632/aging.101414](https://doi.org/10.18632/aging.101414)
2. Liu Z, Kuo PL, Horvath S, Crimmins E, Ferrucci L, Levine M. A new aging measure captures morbidity and
   mortality risk across diverse subpopulations from NHANES IV: A cohort study. *PLOS Medicine*.
   2018;15(12):e1002718. [doi:10.1371/journal.pmed.1002718](https://doi.org/10.1371/journal.pmed.1002718)
    """
)

st.divider()
st.caption("For education only. Not a medical device and not medical advice. This app does not save your values or files.")
