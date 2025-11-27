import io
import pandas as pd
import streamlit as st
from openpyxl import Workbook

st.set_page_config(page_title="Menu Cleaner", layout="wide")
st.title("Menu Cleaner — duplicate GTIN detector (clean output)")

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

# Normalize column names
cols = {c.lower(): c for c in df.columns}
required = ["gtin","merchant_sku","name","category_id"]
for r in required:
    if r not in cols:
        st.error(f"Missing column: {r}")
        st.stop()

gtin_col = cols["gtin"]
sku_col = cols["merchant_sku"]
name_col = cols["name"]
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

# Duplicate keys
df_i["_pair_gtin"] = df_i["_gtin"] + "||" + df_i[cat_col].astype(str)

def build_key_missing(row):
    sku = str(row.get(sku_col, "")).strip()
    name = str(row.get(name_col, "")).strip()
    cat = str(row.get(cat_col, "")).strip()
    return f"{sku}||{name}||{cat}"

df_i["_key_missing"] = df_i.apply(build_key_missing, axis=1)

# Count occurrences
nonempty_pairs = df_i.loc[df_i["_gtin"] != "", "_pair_gtin"]
counts_gtin = nonempty_pairs.value_counts()

missing_rows = df_i.loc[df_i["_gtin"] == "", "_key_missing"]
counts_missing = missing_rows.value_counts()

# Duplicate logic
df_i["_dup_by_gtin"] = df_i.apply(
    lambda r: (r["_gtin"] != "") and (counts_gtin.get(r["_pair_gtin"], 0) > 1), axis=1
)
df_i["_dup_by_missing"] = df_i.apply(
    lambda r: (r["_gtin"] == "") and (counts_missing.get(r["_key_missing"], 0) > 1), axis=1
)

df_i["_dup"] = df_i["_dup_by_gtin"] | df_i["_dup_by_missing"]

# Status text
def status_text(r):
    if r["_missing"] and r["_dup"]:
        return "missing gtin+ duplicate"
    elif r["_missing"]:
        return "missing gtin"
    elif r["_dup"]:
        return "duplicate"
    else:
        return "ok"

df_i["status"] = df_i.apply(status_text, axis=1)

# Choose KEEP vs DELETE (remove duplicates)
df_i["_suggest"] = "KEEP"

# Handle gtin-based groups
group_counter = 1
for key, g in df_i[df_i["_dup_by_gtin"]].groupby("_pair_gtin"):
    idxs = g.index.tolist()
    keeper = idxs[0]
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# Handle missing-gtin duplicate groups
for key, g in df_i[df_i["_dup_by_missing"]].groupby("_key_missing"):
    idxs = g.index.tolist()
    keeper = idxs[0]
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# Build final sheets
full_df = df_i[df_i["_suggest"] == "KEEP"].copy()
dupes_df = df_i[df_i["_suggest"] == "DELETE"].copy()

# Excel export without internal columns
def build_excel(full_df, dupes_df, original_cols):
    out = io.BytesIO()
    wb = Workbook()

    export_cols = original_cols + ["status"]

    # Sheet: Duplicates_Only
    ws1 = wb.active
    ws1.title = "Duplicates_Only"
    ws1.append(export_cols)
    for _, r in dupes_df.iterrows():
        ws1.append([r.get(c, "") for c in export_cols])

    # Sheet: Full_Data
    ws2 = wb.create_sheet("Full_Data")
    ws2.append(export_cols)
    for _, r in full_df.iterrows():
        ws2.append([r.get(c, "") for c in export_cols])

    wb.save(out)
    out.seek(0)
    return out

excel_bytes = build_excel(full_df, dupes_df, df.columns.tolist())

st.download_button("Download cleaned Excel", excel_bytes.getvalue(), "menu_cleaned.xlsx")
