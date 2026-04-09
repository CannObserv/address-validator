# src/address_validator/canada_post_data/directionals.py
"""Bilingual directional lookup for Canadian addresses.

Maps normalised directional tokens (lowercase, no punctuation) to the
Canada Post abbreviated form.  Covers English and French directionals
including compound forms.

Source: Canada Post Addressing Guidelines §3.
"""

CA_DIRECTIONAL_MAP: dict[str, str] = {
    # English — single
    "north": "N",
    "n": "N",
    "south": "S",
    "s": "S",
    "east": "E",
    "e": "E",
    "west": "W",
    "w": "W",
    # English — compound
    "northeast": "NE",
    "ne": "NE",
    "northwest": "NW",
    "nw": "NW",
    "southeast": "SE",
    "se": "SE",
    "southwest": "SW",
    "sw": "SW",
    # French — single
    "nord": "N",
    "sud": "S",
    "est": "E",
    "ouest": "O",  # Canada Post uses O for Ouest
    # French — compound
    "nord-est": "NE",
    "nordest": "NE",
    "nord-ouest": "NO",
    "nordouest": "NO",
    "sud-est": "SE",
    "sudest": "SE",
    "sud-ouest": "SO",
    "sudouest": "SO",
}
