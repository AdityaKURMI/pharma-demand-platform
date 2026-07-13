"""
Phase 2, Step 1: Ingest the FDA NDC Directory -> reference table for drug identity.

The NDC Directory is the FDA's master list of drug products. openFDA
publishes it as a bulk JSON download (better than paginating their API,
which caps out at 25,000 records via skip).

What this produces: data/reference/fda_ndc/fda_ndc.parquet with one row
per drug product: normalized 9-digit NDC key, generic name, brand name,
active ingredients + strengths, dosage form, route, marketing dates.

The join key problem (the heart of Phase 2):
  - SDUD's `ndc` column is an 11-digit string: LLLLL PPPP KK
      (5-digit labeler, 4-digit product, 2-digit package), no dashes.
  - FDA's `product_ndc` is "labeler-product" WITH dashes and WITHOUT
    zero padding, e.g. "0071-0155" (4-4) or "50090-1234" (5-4).
  - Fix: zero-pad labeler to 5 and product to 4 -> a 9-digit key
    "LLLLLPPPP". SDUD side: take first 9 chars of its 11-digit ndc.
    (We drop the 2-digit package code: drug identity doesn't depend on
    package size.)

Run: python ingest_fda_ndc.py
"""

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import requests

BULK_URL = "https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip"
OUT_DIR = Path("data/reference/fda_ndc")


def normalize_product_ndc(product_ndc: str) -> str | None:
    """'50090-1234' or '71-155' style -> 9-digit 'LLLLLPPPP' key."""
    try:
        labeler, product = product_ndc.split("-")
        return labeler.zfill(5) + product.zfill(4)
    except (ValueError, AttributeError):
        return None


def extract_ingredients(rec: dict) -> tuple[str | None, str | None]:
    """Flatten active_ingredients list -> ('atorvastatin', '20 mg/1') style."""
    ingredients = rec.get("active_ingredients") or []
    if not ingredients:
        return None, None
    names = "; ".join(i.get("name", "").lower() for i in ingredients)
    strengths = "; ".join(i.get("strength", "") for i in ingredients)
    return names or None, strengths or None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading FDA NDC Directory bulk file...\n  {BULK_URL}")
    resp = requests.get(BULK_URL, timeout=300)
    resp.raise_for_status()
    print(f"  downloaded {len(resp.content) / 1e6:.1f} MB")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        inner = zf.namelist()[0]
        with zf.open(inner) as f:
            payload = json.load(f)

    records = payload["results"]
    print(f"  {len(records):,} drug product records")

    rows = []
    for rec in records:
        ndc9 = normalize_product_ndc(rec.get("product_ndc", ""))
        ing_names, ing_strengths = extract_ingredients(rec)
        rows.append({
            "ndc9": ndc9,
            "product_ndc_raw": rec.get("product_ndc"),
            "generic_name": (rec.get("generic_name") or "").lower() or None,
            "brand_name": (rec.get("brand_name") or "").lower() or None,
            "active_ingredients": ing_names,
            "strengths": ing_strengths,
            "dosage_form": rec.get("dosage_form"),
            "route": "; ".join(rec.get("route") or []) or None,
            "product_type": rec.get("product_type"),
            "marketing_start": rec.get("marketing_start_date"),
            "marketing_end": rec.get("marketing_end_date"),
            "labeler_name": rec.get("labeler_name"),
        })

    df = pd.DataFrame(rows)
    n_bad_key = df["ndc9"].isna().sum()
    n_dupe = df["ndc9"].duplicated().sum()
    print(f"  rows with unparseable NDC key: {n_bad_key}")
    print(f"  duplicate ndc9 keys: {n_dupe} (multiple package configs / re-registrations)")

    out = OUT_DIR / "fda_ndc.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved -> {out}")


if __name__ == "__main__":
    main()