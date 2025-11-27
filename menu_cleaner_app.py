import io
import pandas as pd
import streamlit as st
from openpyxl import Workbook

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

# Normalize columns
cols = {c.lower(): c for c in df.columns}
required = ["gtin","merchant_sku","name","category_id"]
for r in required:
    if r not in cols:
        st.error(f"Missing column: {r}")
        st.stop()

gtin_col = cols["gtin"]
cat_col = cols["category_id"]

# Normalize GTIN
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

# Duplicate detection
# IMPORTANT FIX:
# compute counts only for rows that have a non-empty GTIN,
# so empty GTIN rows won't be considered duplicates.
nonempty_pairs = df_i.loc[df_i["_gtin"] != "", "_pair"]
counts = nonempty_pairs.value_counts()  # only counts for non-empty gtins
df_i["_dup"] = df_i["_pair"].map(lambda x: counts.get(x, 0) > 1)

# Define status text column (english labels)
def get_status_text(row):
    # note: we intentionally treat missing GTIN as separate from duplicates;
    # a row with missing GTIN will not be marked duplicate even if another row also has missing GTIN+same category
    if row["_missing"] and row["_dup"]:
        return "missing gtin+ duplicate"
    elif row["_missing"]:
        return "missing gtin"
    elif row["_dup"]:
        return "duplicate"
    else:
        return "ok"

df_i["status"] = df_i.apply(get_status_text, axis=1)

# Excel export (no colors, include status column)
def build_excel(df, orig_cols):
    out = io.BytesIO()
    wb = Workbook()

    # Ensure status column included in order
    export_cols = orig_cols + ["status"] if "status" not in orig_cols else orig_cols

    # Duplicates_Only sheet (only problematic rows)
    ws1 = wb.active
    ws1.title = "Duplicates_Only"
    ws1.append(export_cols)

    filt = df[df["status"] != "ok"]
    for _, r in filt.iterrows():
        row = [r.get(c, "") for c in export_cols]
        ws1.append(row)

    # Full_Data sheet (all rows)
    ws2 = wb.create_sheet("Full_Data")
    ws2.append(export_cols)
    for _, r in df.iterrows():
        row = [r.get(c, "") for c in export_cols]
        ws2.append(row)

    wb.save(out)
    out.seek(0)
    return out

excel_bytes = build_excel(df_i, df.columns.tolist() + (["status"] if "status" not in df.columns else []))

st.download_button(
    "Download cleaned Excel",
    excel_bytes.getvalue(),
    "menu_cleaned.xlsx"
)
