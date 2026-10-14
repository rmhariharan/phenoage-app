import math
import streamlit as st

st.title("Biological Age What-If Explorer")

def phenoage(age, albumin, creatinine, glucose, crp, lymph, mcv, rdw, alp, wbc):
    xb = (-19.907
          - 0.0336 * albumin       # g/L
          + 0.0095 * creatinine    # µmol/L
          + 0.1953 * glucose       # mmol/L
          + 0.0954 * math.log(crp) # mg/dL
          - 0.0120 * lymph         # %
          + 0.0268 * mcv           # fL
          + 0.3306 * rdw           # %
          + 0.00188 * alp          # U/L
          + 0.0554 * wbc           # 1000 cells/µL
          + 0.0804 * age)
    g = 0.0076927
    m = 1 - math.exp(-math.exp(xb) * (math.exp(120 * g) - 1) / g)
    return 141.50225 + math.log(-0.00553 * math.log(1 - m)) / 0.09165

st.sidebar.header("Blood markers")
albumin    = st.sidebar.slider("Albumin (g/L)", 30.0, 55.0, 45.0)
creatinine = st.sidebar.slider("Creatinine (µmol/L)", 40.0, 150.0, 80.0)
glucose    = st.sidebar.slider("Glucose (mmol/L)", 3.5, 12.0, 5.0)
crp        = st.sidebar.slider("CRP (mg/dL)", 0.01, 5.0, 0.1)
lymph      = st.sidebar.slider("Lymphocytes (%)", 10.0, 50.0, 30.0)
mcv        = st.sidebar.slider("MCV (fL)", 75.0, 105.0, 90.0)
rdw        = st.sidebar.slider("RDW (%)", 11.0, 18.0, 13.0)
alp        = st.sidebar.slider("Alkaline phosphatase (U/L)", 30.0, 150.0, 70.0)
wbc        = st.sidebar.slider("WBC (1000 cells/µL)", 3.0, 12.0, 6.0)

age = st.slider("Chronological age", 20, 90, 45)

bio = phenoage(age, albumin, creatinine, glucose, crp,
               lymph, mcv, rdw, alp, wbc)

st.metric("Biological age", f"{bio:.1f}", f"{bio - age:+.1f} years",
          delta_color="inverse")