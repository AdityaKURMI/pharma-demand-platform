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