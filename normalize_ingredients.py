"""
Phase 2, Step 4 (final): Normalize drug identity to INGREDIENT (molecule) level.

Problem being solved (finding #12): crosswalk names come from two sources
with incompatible styles —
  - FDA:   salt-level generic names ("albuterol sulfate", "metformin
           hydrochloride", "amlodipine besylate")
  - RxNav: verbose concept strings ("NDA021457 200 ACTUAT albuterol
           0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]")
The same molecule's volume therefore splits across rows. Forecasting needs
one entity per molecule.

Approach, per source:
  - RxNav rows (have an rxcui): query RxNav /rxcui/{id}/related.json?tty=IN
    to get the canonical RxNorm INGREDIENT concept(s). Batch job with the
    same checkpoint/resume pattern as resolve_ndc_rxnav.py. Distinct rxcuis
    only (many NDCs share one rxcui), so this is a few thousand lookups.
  - FDA rows: pragmatic salt/ester-suffix stripping on generic_name
    ("metformin hydrochloride" -> "metformin"). Handles multi-ingredient
    combos by cleaning each component ("acetaminophen and hydrocodone
    bitartrate" -> "acetaminophen; hydrocodone").

Output: data/reference/ndc_crosswalk/ndc_crosswalk_enriched.parquet
  = original crosswalk + `ingredient` column (the forecasting entity).

Run: python normalize_ingredients.py
"""

import re
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

CROSSWALK = "data/reference/ndc_crosswalk/ndc_crosswalk.parquet"
OUT = Path("data/reference/ndc_crosswalk/ndc_crosswalk_enriched.parquet")
CHECKPOINT = Path("data/reference/rxnav_ingredients/rxcui_ingredient.parquet")

API_URL = "https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json"
CHECKPOINT_EVERY = 200
SLEEP_BETWEEN = 0.1

# Common salt/ester/hydrate suffix tokens. Applied repeatedly from the END
# of each ingredient component, so "hydroxyzine hydrochloride" and
# "metoprolol tartrate" both reduce to the base molecule. Deliberately
# conservative — better to under-strip than merge distinct molecules.
SALT_TOKENS = {
    "hydrochloride", "hcl", "sodium", "calcium", "potassium", "dipotassium",
    "magnesium", "besylate", "sulfate", "sulphate", "tartrate", "bitartrate",
    "succinate", "maleate", "mesylate", "citrate", "fumarate", "acetate",
    "bromide", "chloride", "phosphate", "diphosphate", "nitrate", "mononitrate",
    "monohydrate", "dihydrate", "anhydrous", "hemifumarate", "hydrobromide",
    "decanoate", "valerate", "caproate", "palmitate", "stearate", "benzoate",
    "salicylate", "carbonate", "gluconate", "lactate", "oxalate", "tosylate",
    "trihydrate", "sesquihydrate", "propionate",
}

SPLIT_RE = re.compile(r"\s*(?:;|,|/| and )\s*")


def strip_salts(name: str) -> str:
    """'metformin hydrochloride' -> 'metformin'; combos cleaned per part."""
    parts = [p for p in SPLIT_RE.split(name.lower().strip()) if p]
    cleaned = []
    for part in parts:
        tokens = part.split()
        while len(tokens) > 1 and tokens[-1] in SALT_TOKENS:
            tokens.pop()
        cleaned.append(" ".join(tokens))
    # sort for stable identity of combos regardless of listing order
    return "; ".join(sorted(set(cleaned)))


def fetch_ingredient(rxcui: str, session: requests.Session) -> str | None:
    """rxcui -> canonical RxNorm ingredient name(s), tty=IN."""
    for attempt in range(4):
        try:
            resp = session.get(API_URL.format(rxcui=rxcui),
                               params={"tty": "IN"}, timeout=30)
            resp.raise_for_status()
            groups = (resp.json().get("relatedGroup") or {}).get("conceptGroup") or []
            names = [
                c.get("name", "").lower()
                for g in groups if g.get("tty") == "IN"
                for c in (g.get("conceptProperties") or [])
            ]
            names = sorted(set(n for n in names if n))
            return "; ".join(names) if names else None
        except (requests.RequestException, ValueError, TimeoutError, OSError) as e:
            wait = 2 ** attempt
            print(f"    rxcui {rxcui}: attempt {attempt+1} failed "
                  f"({type(e).__name__}); retry in {wait}s")
            time.sleep(wait)
    return None


def resolve_rxnav_ingredients(rxcuis: list[str]) -> pd.DataFrame:
    """Batch lookup with checkpoint/resume, same pattern as the NDC resolver."""
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)

    if CHECKPOINT.exists():
        done = pd.read_parquet(CHECKPOINT)
        todo = [r for r in rxcuis if r not in set(done["rxcui"])]
        print(f"checkpoint found: {len(done):,} rxcuis resolved, {len(todo):,} remaining")
    else:
        done = pd.DataFrame(columns=["rxcui", "ingredient"])
        todo = rxcuis

    session = requests.Session()
    buffer: list[dict] = []
    for i, rxcui in enumerate(todo, start=1):
        buffer.append({"rxcui": rxcui, "ingredient": fetch_ingredient(rxcui, session)})
        time.sleep(SLEEP_BETWEEN)
        if i % CHECKPOINT_EVERY == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(buffer)], ignore_index=True)
            done.to_parquet(CHECKPOINT, index=False)
            buffer = []
            hit = done["ingredient"].notna().sum()
            print(f"[checkpoint] {len(done):,} rxcuis "
                  f"({100.0 * hit / max(len(done),1):.1f}% got an ingredient)")
    return done


def main() -> None:
    con = duckdb.connect()

    # 1. RxNav-sourced rows: rxcui -> ingredient via API
    rxcuis = [r[0] for r in con.execute(f"""
        SELECT DISTINCT rxcui FROM '{CROSSWALK}'
        WHERE source = 'rxnav' AND rxcui IS NOT NULL
    """).fetchall()]
    print(f"{len(rxcuis):,} distinct rxcuis to resolve to ingredients")
    rx_ing = resolve_rxnav_ingredients(rxcuis)

    # 2. Load crosswalk, apply per-source normalization
    xwalk = con.execute(f"SELECT * FROM '{CROSSWALK}'").df()
    xwalk = xwalk.merge(rx_ing, on="rxcui", how="left", suffixes=("", "_rxnav"))

    def pick_ingredient(row) -> str | None:
        if row["source"] == "rxnav" and pd.notna(row.get("ingredient")):
            return row["ingredient"]                     # canonical RxNorm IN
        if pd.notna(row.get("drug_name")):
            return strip_salts(row["drug_name"])         # FDA salt-strip
        return None

    xwalk["ingredient"] = xwalk.apply(pick_ingredient, axis=1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    xwalk.to_parquet(OUT, index=False)

    # 3. Report
    n = len(xwalk)
    n_missing = xwalk["ingredient"].isna().sum()
    n_names = xwalk["drug_name"].nunique()
    n_ingredients = xwalk["ingredient"].nunique()
    print(f"\nCrosswalk rows: {n:,} | missing ingredient: {n_missing}")
    print(f"Distinct drug_name values:  {n_names:,}")
    print(f"Distinct ingredient values: {n_ingredients:,} "
          f"(consolidation: {n_names - n_ingredients:,} name variants merged)")
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()