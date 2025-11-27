# menu_cleaner_app.py -- debug-ready, stricter GTIN cleaning, prefer GTIN keeper
import io
import re
import pandas as pd
import streamlit as st
from openpyxl import Workbook

st.set_page_config(page_title="Menu Cleaner", layout="wide")
st.title("Menu Cleaner — debug-ready (strict GTIN cleaning + keeper priority)")

uploaded = st.file_uploader("Upload CSV or XLSX file", type=["csv","xlsx"])
if uploaded is None:
    st.info("Upload a CSV/XLSX with columns: gtin, merchant_sku, name, category_id (or variant).")
    st.stop()

def read_file(file_obj):
    try:
        return pd.read_excel(file_obj)
    except:
        file_obj.seek(0)
        return pd.read_csv(file_obj)

df = read_file(uploaded)

# -------- helpers --------
def normalize_text(s):
    if pd.isna(s):
        return ""
    s = str(s).strip().lower()
    # replace non-alphanumeric (including punctuation) with space, keep Hebrew letters and digits/letters
    s = re.sub(r'[^0-9a-zא-ת]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def strict_gtin(s):
    """Keep only digits from GTIN-like string. Return empty if none."""
    if pd.isna(s):
        return ""
    s = str(s)
    # remove common suffix .0 etc, then take digits
    s = s.strip()
    # remove BOM/non-printable
    s = re.sub(r'[\u200B-\u200F\uFEFF]', '', s)
    digits = re.sub(r'[^0-9]', '', s)
    return digits

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
# cat_col chosen

# -------- data prep --------
df_i = df.copy()

# strict cleaned GTIN (digits only)
df_i["_gtin"] = df_i[gtin_col].apply(strict_gtin)
df_i["_missing"] = df_i["_gtin"] == ""

# normalized category string
df_i["_category_norm"] = df_i[cat_col].fillna("").astype(str).str.strip()

# raw and normalized keys
def build_raw_key(row):
    sku = str(row.get(sku_col, "")).strip()
    name = str(row.get(name_col, "")).strip()
    cat = str(row.get(cat_col, "")).strip()
    return f"{sku}||{name}||{cat}"

df_i["_key_raw"] = df_i.apply(build_raw_key, axis=1)
df_i["_key_norm"] = df_i.apply(lambda r: f"{normalize_text(r.get(sku_col,''))}||{normalize_text(r.get(name_col,''))}||{normalize_text(r.get(cat_col,''))}", axis=1)

# pair by gtin+category
df_i["_pair_gtin"] = df_i["_gtin"] + "||" + df_i["_category_norm"]

# -------- counts --------
nonempty = df_i.loc[df_i["_gtin"] != "", "_pair_gtin"]
counts_gtin = nonempty.value_counts()

missing_keys = df_i.loc[df_i["_gtin"] == "", "_key_raw"]
missing_keys_filtered = missing_keys[missing_keys.apply(lambda k: k.replace("|","").strip() != "")]
counts_missing = missing_keys_filtered.value_counts()

key_all_series = df_i["_key_norm"]
key_all_filtered = key_all_series[key_all_series.apply(lambda k: k.replace("|","").strip() != "")]
counts_key_all = key_all_filtered.value_counts()

# for normalized key, whether any row has GTIN
key_has_gtin = df_i.groupby("_key_norm")["_gtin"].apply(lambda s: any(s != "")).to_dict()

# -------- duplicate flags --------
df_i["_dup_by_gtin"] = df_i.apply(lambda r: (r["_gtin"] != "") and (counts_gtin.get(r["_pair_gtin"], 0) > 1), axis=1)
df_i["_dup_by_missing"] = df_i.apply(lambda r: (r["_gtin"] == "") and ((r["_key_raw"].replace("|","").strip() != "") and (counts_missing.get(r["_key_raw"], 0) > 1)), axis=1)

def dup_by_key_all_func(row):
    k = row["_key_norm"]
    if k is None or str(k).replace("|","").strip() == "":
        return False
    if counts_key_all.get(k, 0) > 1:
        return True
    if row["_missing"] and key_has_gtin.get(k, False):
        return True
    return False

df_i["_dup_by_key_all"] = df_i.apply(dup_by_key_all_func, axis=1)
df_i["_dup"] = df_i["_dup_by_gtin"] | df_i["_dup_by_missing"] | df_i["_dup_by_key_all"]

# -------- status --------
def compute_status(row):
    if row["_missing"]:
        return "missing gtin+ duplicate" if row["_dup"] else "missing gtin"
    else:
        return "duplicate" if row["_dup"] else "ok"

df_i["status"] = df_i.apply(compute_status, axis=1)

# -------- keeper suggestion (prefer GTIN) --------
df_i["_suggest"] = "KEEP"

# GTIN groups
for key, g in df_i[df_i["_dup_by_gtin"]].groupby("_pair_gtin"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# missing groups
for key, g in df_i[df_i["_dup_by_missing"]].groupby("_key_raw"):
    idxs = g.index.tolist()
    for idx in idxs[1:]:
        df_i.at[idx, "_suggest"] = "DELETE"

# normalized key groups prefer gtin keeper
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

# Full and Duplicates selection
full_df = df_i[df_i["_suggest"] == "KEEP"].copy()
dupes_df = df_i[(df_i["_suggest"] == "DELETE") | (df_i["status"].str.startswith("missing"))].copy()

# sort duplicates: duplicate -> missing+duplicate -> missing
order_map = {"duplicate": 0, "missing gtin+ duplicate": 1, "missing gtin": 2}
dupes_df["__sort"] = dupes_df["status"].map(order_map).fillna(99)
dupes_df = dupes_df.sort_values(["__sort", "_category_norm", name_col, sku_col])
dupes_df = dupes_df.drop(columns="__sort")

# -------- export builder (as before) --------
def build_excel(full_df, dupes_df, original_cols):
    out = io.BytesIO()
    wb = Workbook()
    export_cols = original_cols + ["status"]
    ws1 = wb.active
    ws1.title = "Duplicates_Only"
    ws1.append(export_cols)
    for _, r in dupes_df.iterrows():
        ws1.append([r.get(c, "") for c in export_cols])
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
st.download_button("Download cleaned Excel", excel_bytes.getvalue(), "menu_cleaner_debug_final.xlsx")

# -------- DEBUG PANEL (enter GTIN or example text) --------
st.markdown("---")
st.header("Debug panel — inspect a GTIN / key")

debug_input = st.text_input("Paste GTIN (digits) or any substring of SKU/Name to inspect", value="")
if st.button("Show debug for input") and debug_input.strip() != "":
    q = debug_input.strip()
    # normalize input: digits-only GTIN possibility
    q_digits = re.sub(r'[^0-9]', '', q)
    # find candidate rows:
    if q_digits != "":
        # search by cleaned gtin exact match OR by raw substring in sku/name
        mask = (df_i["_gtin"] == q_digits) | (df_i["_key_raw"].str.contains(q, case=False, na=False)) | (df_i["_key_norm"].str.contains(normalize_text(q), na=False))
    else:
        mask = df_i["_key_raw"].str.contains(q, case=False, na=False) | df_i["_key_norm"].str.contains(normalize_text(q), na=False)
    found = df_i[mask].copy()
    if found.empty:
        st.warning("No rows matched the input. Try the raw SKU or a substring of the name, or the GTIN digits only.")
    else:
        # show useful debug columns and flags
        show_cols = [gtin_col, sku_col, name_col, cat_col, "_gtin", "_key_raw", "_key_norm", "_dup_by_gtin", "_dup_by_missing", "_dup_by_key_all", "_dup", "_missing", "_suggest", "status"]
        # ensure columns exist
        show_cols = [c for c in show_cols if c in found.columns or c in found.columns.tolist()]
        st.dataframe(found[show_cols].reset_index(drop=True))
        # also show group counts for the normalized key(s) found
        keys = found["_key_norm"].unique().tolist()
        st.write("Normalized key counts (key_norm -> total occurrences, has_gtin):")
        for k in keys:
            cnt = counts_key_all.get(k, 0)
            hasg = key_has_gtin.get(k, False)
            st.write(f"- `{k}` → {cnt} rows, has_gtin={hasg}")
        # offer CSV download of debug subset
        buf = io.StringIO()
        found.to_csv(buf, index=False)
        st.download_button("Download debug CSV of matched rows", buf.getvalue(), "debug_rows.csv", mime="text/csv")
