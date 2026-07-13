# Data Profiling Findings — Medicaid SDUD (Weeks 1–2)

Scope: CA, TX, NY — year 2023 (2022 ingestion in progress)

## 1. API returns all columns as VARCHAR
Every field, including numeric ones (number_of_prescriptions,
total_amount_reimbursed, units_reimbursed), arrives as text from the
data.medicaid.gov API.
→ Implication: a typed staging layer with explicit casting is mandatory.
   Built in stage_sdud.py.

## 2. product_name is truncated to 10 characters
e.g. "ATORVASTAT", "AMOXICILLI", "FLUTICASON". The name column cannot be
used as a drug identifier.
→ Implication: drug identity must come from the NDC code joined against
   the FDA NDC Directory / RxNorm (Phase 2 of the project).

## 3. ~39% of CA rows are suppressed (privacy redaction)
CMS hides counts when prescriptions < 11. CA 2023: 39.1% of rows
suppressed. Suppressed rows are low-volume by definition, so their share
of total prescription volume is far smaller than their row share.
→ Implication: treat suppressed rows as censored observations, not
   missing-at-random; document handling + sensitivity analysis in paper.

## 4. Suppression is structural, not random
Suppression rate varies sharply by payment channel and state:
- CA: FFS 31.4% vs MCO 54.9%
- NY: FFS 31.9% vs MCO 51.7%
- TX: FFS 76.4% vs MCO 34.6%  (pattern inverts)
Suppression concentrates in whichever channel is the minor one in that
state.
→ Implication: suppression handling must be channel- and state-aware.

## 5. Drug volume follows a power law — top 500 ≈ 92.3% of volume
CA 2023, unsuppressed, by (truncated) product_name: 3,969 distinct names;
top 500 cover 92.3% of prescriptions, top 100 cover 64.7%, top 50 cover
48.6%.
→ Implication: forecasting the top ~500 drug concepts captures >92% of
   demand — a measured, defensible scoping decision. Recompute after NDC
   resolution (expect slightly higher concentration).

## 6. Published values are clean — 0% quarantine
Staging with TRY_CAST + quarantine across CA/TX/NY 2023 (~441K rows):
0 rows failed numeric casting.
→ Implication: SDUD's data quality issues are structural (suppression,
   truncation, reporting regimes), not malformed values. Quarantine layer
   retained as insurance + proof of cleanliness.

## 7. State payment architectures differ radically
Share of 2023 prescription volume via FFS vs MCO (unsuppressed):
- CA: 91.8% FFS /  8.2% MCO   (Medi-Cal Rx pharmacy carve-out, 2022)
- NY: 74.7% FFS / 25.3% MCO   (blended — see finding 8)
- TX:  1.1% FFS / 98.9% MCO   (fully managed care)
→ Implication: cross-state comparisons and per-channel modeling are
   invalid without accounting for payment structure. This table is a
   candidate Figure 1 for the paper.

## 8. NY has a mid-2023 structural break (NYRx carve-out, Apr 1 2023)
NY quarterly channel mix, 2023 (unsuppressed):
- Q1: 87.5% MCO → Q2: 96.3% FFS (flip between Q1 and Q2)
Total demand stays smooth across the break (Q1 ≈ 19.9M, Q2 ≈ 18.7M,
Q3 ≈ 19.0M, Q4 ≈ 19.2M prescriptions) — only the payment pipe changed.
→ Implication: forecast total demand (FFS + MCO combined) per drug per
   state; single-channel series contain policy-driven regime changes that
   would look like fake demand shocks to a model.

## 9. Long API pulls fail intermittently — resilience must be layered
During the 2022 NY ingestion (~120K rows in), the Medicaid API hit a read
timeout that escaped the original exception handling (TimeoutError at the
socket level, outside requests' exception hierarchy) and crashed the run.
Fixes applied: timeout 60s→120s, retries 4→5 (backoff to 16s), exception
net widened to include TimeoutError/OSError.
Partition-level idempotency allowed recovery by simply re-running: completed
states (CA, TX) were skipped; only NY re-fetched.
→ Implication: two resilience layers are needed — request-level retries
   inside scripts, task-level retries in the orchestrator (Airflow).
   Long batch jobs additionally need checkpointing (applied later in the
   RxNav resolver: progress saved every 500 lookups, resumable).

## 10. FDA NDC Directory alone identifies 87.9% of volume — with recency bias
Joining SDUD (2020–2023, CA/TX/NY) to the FDA NDC Directory on a
normalized 9-digit key (zero-padded labeler+product, package code dropped):
- 67.6% of distinct ndc9 codes matched, but 87.9% of prescription volume
  → unmatched codes skew low-volume
- Match rate by year climbs monotonically: 82.5% (2020) → 86.2% → 89.5%
  → 93.3% (2023)
- Top unmatched NDCs are discontinued products (e.g. Flovent HFA, ProAir
  HFA) and specific manufacturers' exited generic lines (diclofenac,
  metformin, amlodipine) — active, million-prescription products through
  2023 that have since been delisted from the directory.
→ Implication: the FDA directory only reflects currently marketed drugs;
   historical claims data requires a historical dictionary. Also note
   NDC format mismatch (SDUD: 11 digits no dashes; FDA: dashed, unpadded)
   requires key normalization before any join.

## 11. RxNorm historical resolution closes the gap: >99.99% coverage
The 8,560 FDA-unmatched ndc9 codes were batch-resolved via RxNav's
ndcstatus API (checkpointed, rate-limited, resumable): 8,532 (99.7%)
resolved to an RxCUI concept — RxNorm retains mappings for OBSOLETE codes
(e.g. ProAir NDC 59310-0579: status OBSOLETE, still resolves to
"albuterol 0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]", with full
active-date history 2012–2025).
Combined dictionary (FDA + RxNav): 100.0% of prescription volume matched
in every year (2020–2023) at 2-decimal precision; 28 negligible-volume
codes remain

## 12. Crosswalk names come from two incompatible naming styles — volume splits
The crosswalk's drug_name column mixes two source vocabularies:
- FDA rows: salt-level generic names ("albuterol sulfate", "metformin
  hydrochloride", "amlodipine besylate")
- RxNav rows: verbose clinical concept strings ("NDA021457 200 ACTUAT
  albuterol 0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]")
These never group together, so a single molecule's volume splits across
rows — e.g. albuterol's FDA-matched NDCs (20.95M rx) sat apart from
ProAir's RxNav-resolved NDCs (1.6M+ rx). With ~12% of total volume
flowing through RxNav-sourced rows, the split is material, not cosmetic.
Salt-form naming adds a second, smaller splitting axis on the FDA side.
→ Implication: name-level aggregation is untrustworthy; identity must be
   normalized to the molecule (RxNorm ingredient) level before any
   aggregation or modeling.

## 13. Ingredient-level normalization: 5,081 name variants merged;
##     naive aggregation understates top drugs by 13–38%
Method: RxNav /rxcui/{id}/related?tty=IN lookup for rxnav-sourced rows
(3,690 distinct rxcuis, checkpointed batch job); conservative salt/ester
suffix stripping for FDA generic names, with combo drugs split per
component and sorted for stable identity.
Results:
- 22,832 distinct drug_name strings → 17,751 molecule-level ingredients
  (5,081 variants merged); only 3 of 142,919 crosswalk rows left without
  an ingredient.
- Volume corrections at the top of the distribution (4yr, CA/TX/NY):
    albuterol      20.95M → 23.77M rx  (+13%)
    atorvastatin   15.35M → 19.14M rx  (+25%)
    fluticasone    10.57M → 14.62M rx  (+38%)
  i.e. skipping NDC/ingredient resolution understates leading drugs'
  demand by 13–38% — quantifying the cost of the naive approach most
  prior SDUD analyses take.
- Fragmentation extreme: levothyroxine = 372 NDC codes, 31 name variants,
  now one entity.
Caveats: ~10% of rxcuis returned no RxNorm IN concept (kits, packs, some
biologics) and fell back to source names; salt/ester stripping is
deliberately conservative (under-strips rather than risking merging
distinct molecules), and merges ester distinctions like fluticasone
propionate/furoate at molecule level — acceptable for demand forecasting,
noted as a limitation.
→ Implication: the `ingredient` column in ndc_crosswalk_enriched.parquet
   is the canonical forecasting entity: (state, ingredient, quarter).