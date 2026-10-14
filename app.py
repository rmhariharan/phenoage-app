import math

import altair as alt
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
         us=("mg/dL", 0.40, 2.00, 0.90, 0.05, 88.4),
         si=("µmol/L", 35.0, 180.0, 80.0, 1.0, 1.0)),
    dict(key="glucose", name="Glucose (fasting)", coef=0.1953,
         us=("mg/dL", 63.0, 216.0, 90.0, 1.0, 1 / 18.016),
         si=("mmol/L", 3.5, 12.0, 5.0, 0.1, 1.0)),
    dict(key="crp", name="C-reactive protein (CRP)", coef=0.0954,
         us=("mg/L", 0.1, 50.0, 1.0, 0.1, 0.1),
         si=("mg/L", 0.1, 50.0, 1.0, 0.1, 0.1)),
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
         us=("U/L", 30.0, 150.0, 70.0, 1.0, 1.0),
         si=("U/L", 30.0, 150.0, 70.0, 1.0, 1.0)),
    dict(key="wbc", name="White blood cells (WBC)", coef=0.0554,
         us=("×10³/µL", 3.0, 12.0, 6.0, 0.1, 1.0),
         si=("×10⁹/L", 3.0, 12.0, 6.0, 0.1, 1.0)),
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
# 3. THE INTERFACE: header
# ------------------------------------------------------------------
st.title("Biological Age Explorer")
st.markdown(
    "Estimate your biological age from nine routine blood tests, "
    "using the **PhenoAge** formula developed by Morgan Levine and colleagues."
)

# ------------------------------------------------------------------
# 4. THE INTERFACE: sidebar inputs
# ------------------------------------------------------------------
st.sidebar.header("Your numbers")
units = st.sidebar.radio("Lab units", ["US", "SI"], horizontal=True)
system = "us" if units == "US" else "si"

age = st.sidebar.slider("Chronological age (years)", 20, 90, 45)

st.sidebar.caption("Sliders start at typical healthy values. Move them to match your lab report.")

values = {}      # in the formula's units
reference = {}   # reference values, in the formula's units
for m in MARKERS:
    unit, lo, hi, ref, step, factor = m[system]
    shown = st.sidebar.slider(f"{m['name']} ({unit})", lo, hi, ref, step, key=f"{m['key']}_{system}")
    values[m["key"]] = shown * factor
    reference[m["key"]] = ref * factor

# ------------------------------------------------------------------
# 5. THE RESULT
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
# 6. WHICH MARKERS ARE PUSHING IT UP OR DOWN?
# For each marker, reset just that one to its reference value and see
# how much the estimate changes. That change is the marker's effect.
# ------------------------------------------------------------------
st.subheader("What's moving your estimate")

rows = []
for m in MARKERS:
    swapped = dict(values)
    swapped[m["key"]] = reference[m["key"]]
    effect = bio - phenoage(age, swapped)
    rows.append({
        "Marker": m["name"],
        "Years": round(effect, 2),
        "Direction": "Adds years" if effect > 0 else "Takes years off",
    })
df = pd.DataFrame(rows)

chart = (
    alt.Chart(df)
    .mark_bar()
    .encode(
        x=alt.X("Years:Q", title="Years added (+) or taken off (−) versus reference"),
        y=alt.Y("Marker:N", sort=alt.EncodingSortField(field="Years", order="descending"), title=None),
        color=alt.Color(
            "Direction:N",
            scale=alt.Scale(domain=["Adds years", "Takes years off"], range=["#c0392b", "#1f8a70"]),
            legend=alt.Legend(title=None, orient="top"),
        ),
        tooltip=["Marker", "Years"],
    )
    .properties(height=320)
)
st.altair_chart(chart, use_container_width=True)
st.caption(
    "Each bar compares your value with a typical healthy reference value (the slider's starting point). "
    "The bars are approximate and won't add up exactly to the total, because the formula is non-linear."
)

# ------------------------------------------------------------------
# 7. ABOUT, LIMITS, AND REFERENCES
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
st.caption("For education only. Not a medical device and not medical advice. This app does not save the values you enter.")
