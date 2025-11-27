import io
import re
import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import PatternFill

st.set_page_config(page_title="Menu Cleaner", layout="wide")

st.title("Menu Cleaner — duplicate GTIN detector")

uploaded = st.file_uploader("Upload CSV or XLSX file", type=["csv","xlsx"])
if uploaded is None:
    st.stop()

def read_file(file_obj):
    try:
        return pd.read_excel(file_obj)
    except:
        file_obj.seek(0)
        return pd.read_csv(file_obj)

df = read_file(uploaded)

cols = {c.lower(): c for c in df.columns}
required = ["gtin","merchant_sku","name","category_id"]
for r in required:
    if r not in cols:
        st.error(f"Missing column: {r}")
        st.stop()

gtin_col = cols["gtin"]
cat_col = cols["category_id"]

def norm_gtin(v):
    if pd.isna(v):
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s

df_i = df.copy()
df_i["_gtin"] = df_i[gtin_col].apply(norm_gtin)
df_i["_missing"] = df_i["_gtin"] == ""
df_i["_pair"] = df_i["_gtin"] + "||" + df_i[cat_col].astype(str)

counts = df_i["_pair"].value_counts()
df_i["_dup"] = df_i["_pair"].map(lambda x: counts.get(x,0)>1)

from openpyxl import Workbook
from openpyxl.styles import PatternFill
import io

def build_excel(df, orig_cols):
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    orange = PatternFill(start_color="FFD966", end_color="FFD966", fill_type="solid")
    out = io.BytesIO()
    wb = Workbook()

    ws1 = wb.active
    ws1.title="Duplicates_Only"
    ws1.append(orig_cols)
    gtin_idx = orig_cols.index(gtin_col)+1

    filt = df[(df["_missing"]) | (df["_dup"])]
    for _,r in filt.iterrows():
        row=[r.get(c,"") for c in orig_cols]
        ws1.append(row)
        rr=ws1.max_row
        if r["_missing"]:
            ws1.cell(rr,gtin_idx).fill=red
        elif r["_dup"]:
            ws1.cell(rr,gtin_idx).fill=orange

    ws2 = wb.create_sheet("Full_Data")
    ws2.append(orig_cols)
    for _,r in df.iterrows():
        row=[r.get(c,"") for c in orig_cols]
        ws2.append(row)
        rr=ws2.max_row
        if r["_missing"]:
            ws2.cell(rr,gtin_idx).fill=red
        elif r["_dup"]:
            ws2.cell(rr,gtin_idx).fill=orange

    wb.save(out)
    out.seek(0)
    return out

excel_bytes = build_excel(df_i, df.columns.tolist())

st.download_button("Download cleaned Excel", excel_bytes.getvalue(), "menu_cleaned.xlsx")
