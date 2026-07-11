"""
Validation step: run after staging. Fails (non-zero exit) if data looks wrong,
which makes the Airflow task go red — loud failure, the whole point.

Checks per staged partition:
  1. Partition is non-empty
  2. Quarantine rate below threshold (default 5%)
  3. All 4 quarters present
  4. No negative prescription counts

Run: python validate_sdud.py
"""

import sys
from pathlib import Path

import duckdb

STAGING_ROOT = Path("data/staging/sdud")
QUARANTINE_ROOT = Path("data/quarantine/sdud")
MAX_QUARANTINE_PCT = 5.0

def main() -> None:
    con = duckdb.connect()
    staged = sorted(STAGING_ROOT.glob("year=*/state=*/sdud.parquet"))
    if not staged:
        print("VALIDATION FAILED: no staged partitions found")
        sys.exit(1)

    failures: list[str] = []

    for f in staged:
        rel = f.relative_to(STAGING_ROOT).parent
        n_rows, n_quarters, n_negative = con.execute(f"""
            SELECT
                COUNT(*),
                COUNT(DISTINCT quarter),
                SUM(CASE WHEN number_of_prescriptions < 0 THEN 1 ELSE 0 END)
            FROM '{f}'
        """).fetchone()

        q_file = QUARANTINE_ROOT / rel / "sdud_bad_rows.parquet"
        n_bad = con.execute(f"SELECT COUNT(*) FROM '{q_file}'").fetchone()[0] if q_file.exists() else 0
        pct_bad = 100.0 * n_bad / max(n_rows + n_bad, 1)

        problems = []
        if n_rows == 0:
            problems.append("empty partition")
        if n_quarters < 4:
            problems.append(f"only {n_quarters}/4 quarters present")
        if n_negative and n_negative > 0:
            problems.append(f"{n_negative} negative prescription counts")
        if pct_bad > MAX_QUARANTINE_PCT:
            problems.append(f"quarantine rate {pct_bad:.1f}% > {MAX_QUARANTINE_PCT}%")

        status = "FAIL" if problems else "ok"
        print(f"[{status}] {rel}: {n_rows:,} rows, {n_quarters} quarters, "
              f"{pct_bad:.2f}% quarantined" + (f" — {'; '.join(problems)}" if problems else ""))
        if problems:
            failures.append(str(rel))

    if failures:
        print(f"\nVALIDATION FAILED for {len(failures)} partition(s): {failures}")
        sys.exit(1)
    print(f"\nAll {len(staged)} partitions passed validation.")


if __name__ == "__main__":
    main()