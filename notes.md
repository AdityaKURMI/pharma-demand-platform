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

## 14. COVID structural shock at panel start (albuterol case)
Albuterol/CA: Q1-2020 spike to 863K rx (+55% vs following quarter) — 
stockpiling + COVID respiratory use — then crash to 558K in Q2-2020 
(lockdowns, collapsed physician visits) and multi-year recovery through 
2022. n_ndc11_codes drifts ~55 → ~75 over the panel (manufacturer churn).
→ Implication: the panel begins with a once-in-a-century demand shock; 
   forecasting evaluation must account for 2020 explicitly (covid_shock 
   feature; later confirmed better handled by down-weighting 2020 in 
   training — see #17).

## 15. Headline benchmark: 4 models, 4 rolling-origin folds (2023), 
##     1,448 series, metrics on original scale
  model            MASE    sMAPE
  naive            1.077   15.29
  seasonal_naive   1.411   22.01
  ets              0.983   14.35   <- only model beating seasonal-naive
  lgbm_global      1.114   15.86
Target: log1p(prescriptions); global LGBM with lags/rolling/seasonal/
covid features + state/ingredient categoricals; ETS = per-series 
Holt-Winters. Seasonal-naive's poor MASE (1.411) vs plain naive (1.077) 
shows quarterly Medicaid demand is persistence/trend-dominated, not 
seasonality-dominated.

## 16. 2023 = growth cessation + elevated volatility (unwinding partial)
Total panel volume: 2022 grows smoothly 45.2M → 53.2M rx/quarter; 2023 
oscillates 52.2 → 48.1 → 52.9 → 48.5M (±8% quarter-to-quarter swings, 
no sustained trend). Medicaid unwinding (continuous-enrollment protection 
ended 2023-03-31) is a plausible partial driver of the Q2-2023 dip and 
level plateau, but the full Q3 rebound rules out a monotone-decline 
narrative. Per-fold errors confirm whipsaw: naive over-predicts Q2 dip 
(fold1), under-predicts Q3 rebound (fold2: 1.306), over-predicts Q4 dip 
(fold3: 1.309).
→ Implication: the test period's volatility — not a clean regime shift — 
   is what degraded all models; honest framing beats the tidy story.

## 17. Benchmark verdict + ablations: per-series ETS beats global GBM
Reference ETS = 0.983. Ablations (one change at a time):
  A0 baseline               1.114
  A1 drop ingredient cat    1.181  (hurts — ingredient carries signal)
  A2 small model            1.099  (mild help; big help fold0)
  A3 down-weight 2020 @0.2  1.083  (best single change)
  A4 momentum features      1.165  (helps only fold1; noise elsewhere)
  A5a = A2+A3               1.080  (best GBM; frozen final config)
  A5b = A5a+momentum        1.095  (best GBM fold1 = 1.245 — trend 
                                    features help exactly at the regime 
                                    break, cost elsewhere)
GBM deficit concentrates in fold1 (Q2-2023 volatility onset). Effects 
don't compose linearly (original A5 combo: 1.194).
→ Paper conclusion: with short panels (24 quarters) and mid-test 
   structural volatility, classical per-series trend-tracking retains 
   the edge; global GBMs need explicit regime features and pay for them 
   in stable periods. Table frozen — no post-hoc configuration fishing.

## 18. Volume-tier slice: ETS's edge is largest where it matters most
MASE by volume tier (bottom 50% / 50-90% / top 10% of series volume):
  tier          small    mid   large
  ets           0.906  1.077   0.995
  lgbm_global   0.960  1.250   1.345
  naive         0.894  1.225   1.403
Hypothesis "global pooling helps large drugs": REJECTED — GBM is worst 
on the large tier (1.345, barely above naive), while ETS holds ~1.0. 
Likely mechanism: high-volume series have idiosyncratic dynamics 
(formulary shifts, generic entry, 2023 choppiness); pooling shrinks 
predictions toward cross-series average behavior exactly when a series 
moves individually. Small drugs are stable/flat — everything forecasts 
them adequately.
→ Commercially decisive: large drugs are what pharma analytics teams 
   care about, and that's precisely where the per-series approach wins.

## 19. First-ANDA-approval is a structurally noisy LOE proxy — the
##     approval-to-launch gap splits the cohort
Of 134 Orange Book LOE candidates (first generic approval 2019–2022,
incumbent brand predating window), 19 carried >= 10K rx/quarter pre-LOE
in the CA/TX/NY panel. Per-drug price-erosion fits split the cohort in two:
- LAUNCHED generics — genuine erosion: pregabalin/Lyrica (rate 0.96/q,
  floor 0.10 — ~90% gross price collapse), lacosamide/Vimpat (floor 0.15),
  sevelamer (0.56), etonogestrel ring (0.59), ciprofloxacin;dexamethasone.
- APPROVED-BUT-UNLAUNCHED — no erosion, prices continued RISING: apixaban/
  Eliquis (floor 1.16; generics approved 2019, launch delayed to ~2028 by
  patent settlements), empagliflozin/Jardiance (1.40), linagliptin (1.50).
  Plus one proxy artifact: methylphenidate (decades-old generics; a new
  formulation's ANDA masqueraded as first generic entry at molecule level).
Pooled fit across the mixed cohort is meaningless (implied floor 87.9%) —
it averages a cliff with an uphill slope.
→ Implications: (1) ANDA approval != market entry; patent-settlement
   delays make the gap structural, not random noise. Analyses using
   approval dates uncritically conflate two populations. (2) Correct
   anchor = OBSERVED generic entry in utilization data itself (requires
   brand/generic NDC tagging via openFDA application_number — next step).
   (3) The unlaunched cohort is retained as a named contrast group;
   its continued price inflation is a result, not a nuisance.

## 20. Brands raise prices into the patent cliff (pre-LOE inflation)
Event-time price index (anchored to t=-4..-1 mean) sits BELOW 1.0 in
earlier pre-LOE quarters: t=-8..-5 means of 0.908/0.939/0.921/0.904 vs
~1.0 at t=-1 — i.e. gross cost-per-prescription rose ~10-13% over the
two years approaching first generic approval, across the 19-event cohort.
Consistent with documented brand pricing behavior ahead of exclusivity
loss ("harvest pricing").
→ Caveat for both #19/#20: SDUD amounts are pre-rebate; brand rebates are
   large and confidential, so gross-price levels overstate net prices and
   measured erosion magnitude is a lower bound on net erosion. Curve
   SHAPE and TIMING remain informative.