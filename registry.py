"""
Registry of SDUD dataset IDs on data.medicaid.gov.

Each year of State Drug Utilization Data is a separate dataset with its own
UUID. To add more years: go to https://data.medicaid.gov, search
"State Drug Utilization Data <year>", open the dataset page, and copy the
UUID from the URL (data.medicaid.gov/dataset/<UUID>).
"""

SDUD_DATASETS: dict[int, str] = {
    2022: "200c2cba-e58d-4a95-aa60-14b99736808d",
    2023: "d890d3a9-6b00-43fd-8b31-fcba4c8e2909",
    2024: "61729e5a-7aa8-448c-8903-ba3e0cd0ea3c",
    2025: "158a1baa-5506-400a-8ec3-97756f0b0536",
    # TODO(you): add 2018-2021 by looking up their UUIDs on data.medicaid.gov
}

# Start with a small, deliberately diverse set of states:
# CA (pharmacy carve-out / FFS-heavy), TX (managed-care-heavy), NY (large,
# mixed), OH (mid-size), plus expand later.
DEFAULT_STATES = ["CA", "TX", "NY", "OH"]