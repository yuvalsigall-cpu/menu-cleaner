import io
import re
import pandas as pd
import streamlit as st
from openpyxl import Workbook

st.set_page_config(page_title="Menu Cleaner", layout="wide")
st.title("Menu Cleaner — final (robust key matching, prefer GTIN keeper)")

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

# helper: normalize text for robust matching
def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s).strip().lower()
    # replace non-alphanumeric with space
    s = re.sub(r'[^0-9a-zא-ת]+', ' ', s)
    # collapse spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Normalize column names (map lowercase -> original)
cols = {c.strip().lower(): c for c in df.columns}

# detect category column flexibly
possible_category_keys = ["category_id", "category", "category name", "category_name", "cat", "cat_id", "category-id", "catid"]
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
# cat_col determined

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

# Build pair by GTIN+category (for gtin-based duplicates)
df_i["_pair_gtin"] = df_i["_gtin"] + "||" + df_i["_category_norm"]

# Build raw key (sku+name+category) and a normalized version for robust matching
def build_raw_key(row):
    sku = str(row.get(sku_col, "")).strip()
    name = str(row.get(name_col, "")).strip()
    cat = str(row.get(cat_col, "")).strip()
    return f"{sku}||{name}||{cat}"

df_i["_key_raw"] = df_i.apply(build_raw_key, axis=1)
df_i["_key_norm"] = df_i.apply(lambda r: f"{normalize_text(r.get(sku_col,''))}||{normalize_text(r.get(name_col,''))}||{normalize_text(r.get(cat_col,''))}", axis=1)

# --- counts ---
nonempty = df_i.loc[df_i["_gtin"] != "", "_pair_gtin"]
counts_gtin = nonempty.value_counts()

missing_keys = df_i.loc[df_i["_gtin"] == "", "_key_raw"]
missing_keys_filtered = missing_keys[missing_keys.apply(lambda k: k.replace("|","").strip() != "")]
counts_missing = missing_keys_filtered.value_counts()

# counts on normalized key across ALL rows
key_all_series = df_i["_key_norm"]
key_all_filtered = key_all_series[key_all_series.apply(lambda k: k.replace("|","").strip() != "")]
counts_key_all = key_all_filtered.value_counts()

# also compute whether a normalized key has at least one row with GTIN
key_has_gtin = df_i.groupby("_key_norm")["_gtin"].apply(lambda s: any(s != "")).to_dict()

# Duplicate flags
df_i["_dup_by_gtin"] = df_i.apply(lambda r: (r["_gtin"] != "") and (counts_gtin.get(r["_pair_gtin"], 0) > 1), axis=1)

df_i["_dup_by_missing"] = df_i.apply(lambda r: (r["_gtin"] == "") and ((r["_key_raw"].replace("|","").strip() != "") and (counts_missing.get(r["_key_raw"], 0) > 1)), axis=1)

# NEW: dup by normalized key across all rows (robust); for missing rows also check if any other row with same normalized key has GTIN
def dup_by_key_all_func(row):
    k = row["_key_norm"]
    if k is None or str(k).replace("|","").strip() == "":
        return False
    # if more than 1 occurrence of normalized key -> duplicate
    if counts_key_all.get(k, 0) > 1:
        return True
    # if current row is missing GTIN but there exists another row with same normalized key that has GTIN -> treat as duplicate
    if row["_missing"] and key_has_gtin.get(k, False):
        # but ensure that the only row with GTIN is not the current one (current is missing so OK)
        return True
    return False

df_i["_dup_by_key_all"] = df_i.apply(dup_by_key_all_func, axis=1)

df_i["_dup"] = df_i["_dup_by_gtin"] | df_i["_dup_by_missing"] | df_i["_dup_by_key_all"]

# Deterministic status
def compute_status(row):
    if row["_missing"]:
        return "missing gtin+ duplicate" if row["_dup"] else "missing gtin"
    else:
        return "duplicate" if row["_dup"] else "ok"

df_i["status"] = df_i.apply(compute_status, axis=1)

# Suggest KEEP/DELETE: default KEEP
df_i["_suggest"] = "KEEP"

# 1) GTIN groups -> mark extras DELETE (keep first)
for key, g in df_i[df_i["_dup_by_gtin"]].groupby("_pair_gtin"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# 2) Missing-GTIN groups (by raw key) -> mark extras DELETE (keep first)
for key, g in df_i[df_i["_dup_by_missing"]].groupby("_key_raw"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# 3) Normalized-key groups (key_norm) -> choose keeper with priority:
# prefer a row that has GTIN; else prefer existing KEEP; else first
for key, g in df_i[df_i["_key_norm"].map(lambda k: counts_key_all.get(k,0)>1)].groupby("_key_norm"):
    idxs = g.index.tolist()
    keeper = None
    for i in idxs:
        if df_i.at[i, "_gtin"] != "":
            keeper = i
            break
    if keeper is None:
        for i in idxs:
            if df_i.at[i, "_suggest"] == "KEEP":
                keeper = i
                break
    if keeper is None:
        keeper = idxs[0]
    for idx in idxs:
        if idx != keeper:
            df_i.at[idx, "_suggest"] = "DELETE"

# Full_Data: keep rows suggested KEEP (one per product)
full_df = df_i[df_i["_suggest"] == "KEEP"].copy()

# Duplicates_Only: show redundant copies (suggest==DELETE) and also any missing rows (so we get missing-only and missing+duplicate)
dupes_df = df_i[ (df_i["_suggest"] == "DELETE") | (df_i["status"].str.startswith("missing")) ].copy()

# Sorting order for Duplicates_Only:
order_map = {"duplicate": 0, "missing gtin+ duplicate": 1, "missing gtin": 2}
dupes_df["__sort"] = dupes_df["status"].map(order_map).fillna(99)
dupes_df = dupes_df.sort_values(["__sort", "_category_norm", name_col, sku_col])
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
st.download_button("Download cleaned Excel", excel_bytes.getvalue(), "menu_cleaner_final_robust.xlsx")
