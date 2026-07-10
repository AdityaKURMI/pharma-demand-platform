"""
Registry of SDUD dataset IDs on data.medicaid.gov.

Each year of State Drug Utilization Data is a separate dataset with its own
UUID. To add more years: go to https://data.medicaid.gov, search
"State Drug Utilization Data <year>", open the dataset page, and copy the
UUID from the URL (data.medicaid.gov/dataset/<UUID>).
"""

SDUD_DATASETS: dict[int, str] = {
    2020: "cc318bfb-a9b2-55f3-a924-d47376b32ea3",
    2021: "eec7fbe6-c4c4-5915-b3d0-be5828ef4e9d",
    2022: "200c2cba-e58d-4a95-aa60-14b99736808d",
    2023: "d890d3a9-6b00-43fd-8b31-fcba4c8e2909",
    2024: "61729e5a-7aa8-448c-8903-ba3e0cd0ea3c",
    2025: "158a1baa-5506-400a-8ec3-97756f0b0536",
}

# Start with a small, deliberately diverse set of states:
# CA (pharmacy carve-out / FFS-heavy), TX (managed-care-heavy), NY (large,
# mixed), OH (mid-size), plus expand later.
DEFAULT_STATES = ["CA", "TX", "NY", "OH"]