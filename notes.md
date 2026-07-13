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

## 12 (short version):
 "Crosswalk names come from two sources with incompatible styles (FDA salt-level generic names vs RxNav verbose concept strings), splitting some drugs' volume across rows — e.g. albuterol's FDA-matched NDCs vs ProAir's RxNav-resolved ones. Ingredient-level normalization via RxNorm is required before aggregation is trustworthy."