import io
import pandas as pd
import streamlit as st
from openpyxl import Workbook

st.set_page_config(page_title="Menu Cleaner", layout="wide")
st.title("Menu Cleaner — duplicate GTIN detector (sorted duplicates)")

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

# Normalize column names (map lowercase -> original)
cols = {c.strip().lower(): c for c in df.columns}

# detect category column flexibly
possible_category_keys = ["category_id", "category", "category name", "category_name", "cat", "cat_id"]
cat_col = None
for k in possible_category_keys:
    if k in cols:
        cat_col = cols[k]
        break

required_base = ["gtin","merchant_sku","name"]
missing_required = [r for r in required_base if r not in cols]
if missing_required:
    st.error(f"Missing required column(s): {missing_required}. Needed: gtin, merchant_sku, name, and category (or category_id).")
    st.stop()

if cat_col is None:
    st.error("Missing category column. Expected one of: category_id, category, category_name.")
    st.stop()

gtin_col = cols["gtin"]
sku_col  = cols["merchant_sku"]
name_col = cols["name"]
# cat_col already set

# Normalize GTIN
def norm_gtin(v):
    if pd.isna(v):
        return ""
    s = str(v).strip()
    if s.lower() == "nan" or s == "":
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s

df_i = df.copy()
df_i["_gtin"] = df_i[gtin_col].apply(norm_gtin)
df_i["_missing"] = df_i["_gtin"] == ""

# Normalize category string for consistent pairing
df_i["_category_norm"] = df_i[cat_col].fillna("").astype(str).str.strip()

# Build keys
df_i["_pair_gtin"] = df_i["_gtin"] + "||" + df_i["_category_norm"]

def missing_key(row):
    sku = str(row.get(sku_col, "")).strip()
    name = str(row.get(name_col, "")).strip()
    cat = str(row.get(cat_col, "")).strip()
    return f"{sku}||{name}||{cat}"

df_i["_key_missing"] = df_i.apply(missing_key, axis=1)

# Counts (robust)
nonempty = df_i.loc[df_i["_gtin"] != "", "_pair_gtin"]
counts_gtin = nonempty.value_counts()

missing_keys = df_i.loc[df_i["_gtin"] == "", "_key_missing"]
missing_keys_filtered = missing_keys[missing_keys.apply(lambda k: k.replace("|","").strip() != "")]
counts_missing = missing_keys_filtered.value_counts()

# Duplicate flags
df_i["_dup_by_gtin"] = df_i.apply(lambda r: (r["_gtin"] != "") and (counts_gtin.get(r["_pair_gtin"], 0) > 1), axis=1)
df_i["_dup_by_missing"] = df_i.apply(lambda r: (r["_gtin"] == "") and ((r["_key_missing"].replace("|","").strip() != "") and (counts_missing.get(r["_key_missing"], 0) > 1)), axis=1)
df_i["_dup"] = df_i["_dup_by_gtin"] | df_i["_dup_by_missing"]

# Deterministic status
def compute_status(row):
    if row["_missing"]:
        return "missing gtin+ duplicate" if row["_dup"] else "missing gtin"
    else:
        return "duplicate" if row["_dup"] else "ok"

df_i["status"] = df_i.apply(compute_status, axis=1)

# Suggest KEEP/DELETE: keep first occurrence per group
df_i["_suggest"] = "KEEP"

for key, g in df_i[df_i["_dup_by_gtin"]].groupby("_pair_gtin"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

for key, g in df_i[df_i["_dup_by_missing"]].groupby("_key_missing"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# Full_Data: keep rows suggested KEEP (one per product)
full_df = df_i[df_i["_suggest"] == "KEEP"].copy()

# Duplicates_Only: include redundant copies OR non-duplicate missing-gtin rows
dupes_df = df_i[ (df_i["_suggest"] == "DELETE") | ( (df_i["status"].str.startswith("missing")) & (~df_i["_dup"]) ) ].copy()

# Sorting order for Duplicates_Only:
# 1) missing gtin
# 2) missing gtin+ duplicate
# 3) duplicate
order_map = {
    "missing gtin": 0,
    "missing gtin+ duplicate": 1,
    "duplicate": 2
}
dupes_df["__sort"] = dupes_df["status"].map(order_map).fillna(99)
# secondary sorting: by category then name for readability
dupes_df = dupes_df.sort_values(["__sort", "_category_norm", name_col])
dupes_df = dupes_df.drop(columns="__sort")

# Export: original columns + status (no internal helper columns)
def build_excel(full_df, dupes_df, original_cols):
    out = io.BytesIO()
    wb = Workbook()

    export_cols = original_cols + ["status"]

    # Duplicates_Only sheet
    ws1 = wb.active
    ws1.title = "Duplicates_Only"
    ws1.append(export_cols)
    for _, r in dupes_df.iterrows():
        ws1.append([r.get(c, "") for c in export_cols])

    # Full_Data sheet
    ws2 = wb.create_sheet("Full_Data")
    ws2.append(export_cols)
    for _, r in full_df.iterrows():
        ws2.append([r.get(c, "") for c in export_cols])

    wb.save(out)
    out.seek(0)
    return out

original_columns = df.columns.tolist()
excel_bytes = build_excel(full_df, dupes_df, original_columns)

st.write(f"Rows total: {len(df_i)} — Kept: {len(full_df)} — Problematic shown: {len(dupes_df)}")
st.download_button("Download cleaned Excel", excel_bytes.getvalue(), "menu_cleaner_compact_sorted.xlsx")
