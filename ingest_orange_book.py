"""
LOE chapter, Step 1: Ingest the FDA Orange Book and detect LOE candidates.

The Orange Book is the FDA's registry of approved drug products with
therapeutic equivalence evaluations. Key file: products.txt (~-delimited),
one row per approved product, including:
  - Appl_Type: 'N' = NDA (brand/innovator), 'A' = ANDA (generic)
  - Approval_Date
  - Ingredient, Trade_Name, Applicant

LOE detection logic (pragmatic, data-driven):
  A molecule's practical loss of exclusivity is well proxied by its FIRST
  GENERIC (ANDA) APPROVAL date. We flag molecules whose first ANDA
  approval falls inside our observation window with enough runway on both
  sides (>= 4 quarters before and after) to fit erosion curves.

Outputs:
  data/reference/orange_book/products.parquet      (full parsed table)
  data/reference/orange_book/loe_candidates.parquet (molecule, first ANDA
      approval date, n prior brand products, first brand approval year)

Run: python ingest_orange_book.py
"""

import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

# The FDA "Orange Book Data Files" zip. If this URL 404s (FDA reshuffles
# media IDs occasionally), download manually from:
#   https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files
# and place the zip at data/reference/orange_book/EOBZIP.zip
ZIP_URL = "https://www.fda.gov/media/76860/download?attachment"
OUT_DIR = Path("data/reference/orange_book")
LOCAL_ZIP = OUT_DIR / "EOBZIP.zip"

WINDOW_START = "2019-01-01"   # first ANDA must fall in here: leaves >= 4
WINDOW_END = "2022-12-31"     # quarters of 2018-2023 panel on each side


def get_zip_bytes() -> bytes:
    if LOCAL_ZIP.exists():
        print(f"Using local zip: {LOCAL_ZIP}")
        return LOCAL_ZIP.read_bytes()
    print(f"Downloading Orange Book zip...\n  {ZIP_URL}")
    resp = requests.get(ZIP_URL, timeout=300,
                        headers={"User-Agent": "pharma-demand-platform/1.0"})
    resp.raise_for_status()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_ZIP.write_bytes(resp.content)   # cache for idempotent re-runs
    return resp.content


def parse_products(zip_bytes: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        name = next(n for n in zf.namelist() if n.lower().startswith("products"))
        with zf.open(name) as f:
            df = pd.read_csv(f, sep="~", dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    df["ingredient"] = df["ingredient"].str.lower().str.strip()
    # Approval dates like 'Apr 23, 1998' or 'Approved Prior to Jan 1, 1982'
    df["approval_date"] = pd.to_datetime(
        df["approval_date"], format="%b %d, %Y", errors="coerce")
    return df


def detect_loe_candidates(products: pd.DataFrame) -> pd.DataFrame:
    grp = products.groupby("ingredient")

    first_anda = (products[products["appl_type"] == "A"]
                  .groupby("ingredient")["approval_date"].min()
                  .rename("first_generic_approval"))
    first_nda = (products[products["appl_type"] == "N"]
                 .groupby("ingredient")["approval_date"].min()
                 .rename("first_brand_approval"))
    n_brand = (products[products["appl_type"] == "N"]
               .groupby("ingredient").size().rename("n_brand_products"))

    cand = pd.concat([first_anda, first_nda, n_brand], axis=1).reset_index()

    cand = cand[
        cand["first_generic_approval"].between(WINDOW_START, WINDOW_END)
        & cand["first_brand_approval"].notna()
        # brand must predate the window: a real incumbent losing exclusivity,
        # not a brand+generic co-launch
        & (cand["first_brand_approval"] < WINDOW_START)
    ].copy()

    cand["loe_quarter_idx"] = (
        (cand["first_generic_approval"].dt.year - 2018) * 4
        + cand["first_generic_approval"].dt.quarter - 1
    )
    return cand.sort_values("first_generic_approval").reset_index(drop=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    products = parse_products(get_zip_bytes())
    print(f"Orange Book products: {len(products):,} rows, "
          f"{products['ingredient'].nunique():,} distinct ingredients")
    print(f"  NDA (brand) rows: {(products['appl_type'] == 'N').sum():,} | "
          f"ANDA (generic) rows: {(products['appl_type'] == 'A').sum():,}")

    products.to_parquet(OUT_DIR / "products.parquet", index=False)

    cand = detect_loe_candidates(products)
    cand.to_parquet(OUT_DIR / "loe_candidates.parquet", index=False)
    print(f"\nLOE candidates (first generic approval {WINDOW_START[:4]}-"
          f"{WINDOW_END[:4]}, brand incumbent predates window): {len(cand)}")
    print(cand.head(20).to_string())


if __name__ == "__main__":
    main()