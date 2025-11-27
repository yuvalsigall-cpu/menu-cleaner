import io
import pandas as pd
import streamlit as st
from openpyxl import Workbook

st.set_page_config(page_title="Menu Cleaner", layout="wide")
st.title("Menu Cleaner — duplicate GTIN detector (dedupe & keep-one)")

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

# Normalize column names (case-insensitive)
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

# keys
df_i["_pair_gtin"] = df_i["_gtin"] + "||" + df_i[cat_col].astype(str)

def build_key_for_missing(row):
    sku = str(row.get(sku_col, "")).strip()
    name = str(row.get(name_col, "")).strip()
    cat = str(row.get(cat_col, "")).strip()
    return f"{sku}||{name}||{cat}"

df_i["_key_missing"] = df_i.apply(build_key_for_missing, axis=1)

# counts
nonempty_pairs = df_i.loc[df_i["_gtin"] != "", "_pair_gtin"]
counts_gtin = nonempty_pairs.value_counts()

missing_rows_keys = df_i.loc[df_i["_gtin"] == "", "_key_missing"]
counts_key = missing_rows_keys.value_counts()

# determine duplicate flags:
df_i["_dup_by_gtin"] = df_i.apply(lambda r: (r["_gtin"] != "") and (counts_gtin.get(r["_pair_gtin"], 0) > 1), axis=1)
df_i["_dup_by_key"] = df_i.apply(lambda r: (r["_gtin"] == "") and (counts_key.get(r["_key_missing"], 0) > 1), axis=1)
df_i["_dup"] = df_i["_dup_by_gtin"] | df_i["_dup_by_key"]

# assign groups and suggestion KEEP/DELETE: keep first occurrence in each duplicate group
df_i["_group_id"] = None
df_i["_suggest"] = "KEEP"
group_counter = 1

# handle GTIN-based groups
for key, g in df_i[df_i["_dup_by_gtin"]].groupby("_pair_gtin"):
    idxs = g.index.tolist()
    gid = f"gtin_grp_{group_counter}"
    group_counter += 1
    # keep first, mark others as DELETE
    keeper = idxs[0]
    for i, idx in enumerate(idxs):
        df_i.at[idx, "_group_id"] = gid
        if idx != keeper:
            df_i.at[idx, "_suggest"] = "DELETE"
    # store duplicates count
    df_i.loc[idxs, "duplicates_in_group"] = len(idxs)

# handle missing-GTIN key-based groups
for key, g in df_i[df_i["_dup_by_key"]].groupby("_key_missing"):
    idxs = g.index.tolist()
    gid = f"miss_grp_{group_counter}"
    group_counter += 1
    keeper = idxs[0]
    for idx in idxs:
        df_i.at[idx, "_group_id"] = gid
        if idx != keeper:
            df_i.at[idx, "_suggest"] = "DELETE"
    df_i.loc[idxs, "duplicates_in_group"] = len(idxs)

# ensure duplicates_in_group column exists (fill 1 for non-group rows)
if "duplicates_in_group" not in df_i.columns:
    df_i["duplicates_in_group"] = df_i.get("duplicates_in_group", 1).fillna(1).astype(int)
else:
    df_i["duplicates_in_group"] = df_i["duplicates_in_group"].fillna(1).astype(int)

# status text
def status_text(row):
    missing = row["_missing"]
    dup = row["_dup"]
    if missing and dup:
        return "missing gtin+ duplicate"
    elif missing:
        return "missing gtin"
    elif dup:
        return "duplicate"
    else:
        return "ok"

df_i["status"] = df_i.apply(status_text, axis=1)

# Build outputs:
# Full_Data: keep only rows with _suggest == KEEP (one per group), plus all non-duplicate rows
full_df = df_i[df_i["_suggest"] == "KEEP"].copy()

# For rows that were never part of a counted group, duplicates_in_group may be NaN -> set to 1
full_df["duplicates_in_group"] = full_df["duplicates_in_group"].fillna(1).astype(int)

# Duplicates_Only: rows suggested for deletion (the extra copies) + rows that are missing GTIN but marked duplicate (they will be in groups)
dupes_df = df_i[df_i["_suggest"] == "DELETE"].copy()

# Also include rows with status != ok but _suggest == KEEP? (e.g., single missing GTIN) -> we do NOT include them in Duplicates_Only
# Duplicates_Only is purely the redundant rows to review.

# Prepare Excel (include helper columns in export)
def build_excel_export(full_df, dupes_df, original_cols):
    out = io.BytesIO()
    wb = Workbook()

    export_cols = original_cols + ["status", "_group_id", "duplicates_in_group"]

    # Duplicates_Only sheet
    ws1 = wb.active
    ws1.title = "Duplicates_Only"
    ws1.append(export_cols)
    for _, r in dupes_df.iterrows():
        row = [r.get(c, "") for c in export_cols]
        ws1.append(row)

    # Full_Data sheet
    ws2 = wb.create_sheet("Full_Data")
    ws2.append(export_cols)
    for _, r in full_df.iterrows():
        row = [r.get(c, "") for c in export_cols]
        ws2.append(row)

    wb.save(out)
    out.seek(0)
    return out

original_columns = df.columns.tolist()
excel_bytes = build_excel_export(full_df, dupes_df, original_columns)

st.write(f"Rows total: {len(df_i)} — Unique kept: {len(full_df)} — Redundant copies: {len(dupes_df)}")
st.download_button("Download cleaned Excel (keeps one per product)", excel_bytes.getvalue(), "menu_cleaned_deduped.xlsx")
