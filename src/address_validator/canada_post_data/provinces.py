# src/address_validator/canada_post_data/provinces.py
"""Canada Post province and territory lookup table.

Maps both full names (uppercase) and 2-letter abbreviations to the
official Canada Post 2-letter abbreviation.  All lookups must be
performed on uppercased input.

Source: Canada Post Addressing Guidelines, Table 1.
"""

# Keys: uppercase full name or abbreviation.  Values: 2-letter abbreviation.
PROVINCE_MAP: dict[str, str] = {
    # Abbreviation → abbreviation (identity; for normalising already-abbreviated input)
    "AB": "AB",
    "BC": "BC",
    "MB": "MB",
    "NB": "NB",
    "NL": "NL",
    "NS": "NS",
    "NT": "NT",
    "NU": "NU",
    "ON": "ON",
    "PE": "PE",
    "QC": "QC",
    "SK": "SK",
    "YT": "YT",
    # Full name → abbreviation
    "ALBERTA": "AB",
    "BRITISH COLUMBIA": "BC",
    "MANITOBA": "MB",
    "NEW BRUNSWICK": "NB",
    "NEWFOUNDLAND AND LABRADOR": "NL",
    "NEWFOUNDLAND": "NL",
    "LABRADOR": "NL",
    "NOVA SCOTIA": "NS",
    "NORTHWEST TERRITORIES": "NT",
    "NUNAVUT": "NU",
    "ONTARIO": "ON",
    "PRINCE EDWARD ISLAND": "PE",
    "QUEBEC": "QC",
    "QUÉBEC": "QC",
    "SASKATCHEWAN": "SK",
    "YUKON": "YT",
    "YUKON TERRITORY": "YT",
}
