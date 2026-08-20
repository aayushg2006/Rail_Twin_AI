"""Which corridor out of Vasai Road Jn a station lies on.

Vasai Road has three working directions:

  NORTH  Western main towards Nalla Sopara / Virar / Dahanu Rd and onward to
         Surat, Gujarat, Rajasthan and Delhi.
  SOUTH  Western main towards Bhayandar / Borivali / Mumbai.
  DIVA   the Vasai Road - Diva branch, and everything beyond it: Panvel, Kalyan,
         Pune, the Konkan route and South India.

A service through BSR therefore has an ARRIVAL corridor (from its origin) and a
DEPARTURE corridor (towards its destination); the pair selects its route through
the junction. Codes below are the ones that actually occur in the two exports.
"""
from __future__ import annotations

NORTH = "NORTH"
SOUTH = "SOUTH"
DIVA = "DIVA"

# Destination / origin station codes seen in the exports.
STATION_REGION: dict[str, str] = {
    # --- Western main, north of Vasai Road -------------------------------
    "NSP": NORTH, "VR": NORTH, "VTN": NORTH, "BOR": NORTH, "DRD": NORTH,
    "UDN": NORTH, "ST": NORTH, "BL": NORTH, "VS": NORTH, "BRC": NORTH,
    "ADI": NORTH, "VTA": NORTH, "RJT": NORTH, "PBR": NORTH, "BVC": NORTH,
    "OKHA": NORTH, "JAM": NORTH, "GIMB": NORTH, "BME": NORTH, "BKN": NORTH,
    "SGNR": NORTH, "HSR": NORTH, "ASR": NORTH, "HW": NORTH, "NZM": NORTH,
    "CDG": NORTH, "UDZ": NORTH, "AII": NORTH, "GWL": NORTH, "INDB": NORTH,
    "YNRK": NORTH, "JU": NORTH, "JP": NORTH, "DEE": NORTH, "BGKT": NORTH,
    # --- Western main, south of Vasai Road (Mumbai) ----------------------
    "BYR": SOUTH, "MIRA": SOUTH, "DIC": SOUTH, "BVI": SOUTH, "ADH": SOUTH,
    "BA": SOUTH, "BDTS": SOUTH, "DDR": SOUTH, "MMCT": SOUTH, "CCG": SOUTH,
    "BCT": SOUTH, "STR": SOUTH,
    # --- via the Diva branch: Panvel / Kalyan / Pune / Konkan / South -----
    "DIVA": DIVA, "PNVL": DIVA, "ROHA": DIVA, "KYN": DIVA, "CSMT": DIVA,
    "PUNE": DIVA, "DD": DIVA, "KK": DIVA, "MRJ": DIVA, "KOP": DIVA,
    "SUR": DIVA, "RN": DIVA, "SWV": DIVA, "MAO": DIVA, "KAWR": DIVA,
    "TOK": DIVA, "MAJN": DIVA, "CAN": DIVA, "ERS": DIVA, "TVC": DIVA,
    "TVCN": DIVA, "TEN": DIVA, "MAS": DIVA, "YPR": DIVA, "SBC": DIVA,
    "MYS": DIVA, "CBE": DIVA, "TPJ": DIVA, "KWP": DIVA, "HDP": DIVA,
    "EKNR": DIVA, "NED": DIVA, "SC": DIVA, "GTL": DIVA, "UBL": DIVA,
}

# Origin phrases as they appear in train names, longest match first.
ORIGIN_REGION: list[tuple[str, str]] = [
    ("nalla sopara", NORTH), ("dahanu road", NORTH), ("boisar", NORTH),
    ("virar", NORTH), ("valsad", NORTH), ("udhna", NORTH), ("surat", NORTH),
    ("vadodara", NORTH), ("ahmedabad", NORTH), ("rajkot", NORTH),
    ("porbandar", NORTH), ("bhavnagar", NORTH), ("okha", NORTH),
    ("jamnagar", NORTH), ("gandhidham", NORTH), ("barmer", NORTH),
    ("bikaner", NORTH), ("hisar", NORTH), ("amritsar", NORTH),
    ("haridwar", NORTH), ("nizamuddin", NORTH), ("chandigarh", NORTH),
    ("udaipur", NORTH), ("ajmer", NORTH), ("gwalior", NORTH),
    ("indore", NORTH), ("jaipur", NORTH), ("jodhpur", NORTH),
    ("bhagat ki kothi", NORTH), ("yog nagari", NORTH), ("rishikesh", NORTH),
    ("bhuj", NORTH), ("veraval", NORTH), ("hapa", NORTH), ("bhayandar", SOUTH),
    ("borivali", SOUTH), ("andheri", SOUTH), ("bandra", SOUTH),
    ("dadar", SOUTH), ("mumbai central", SOUTH), ("mumbai ctrl", SOUTH),
    ("churchgate", SOUTH), ("dombivli", DIVA), ("diva", DIVA),
    ("panvel", DIVA), ("kalyan", DIVA), ("roha", DIVA), ("pune", DIVA),
    ("daund", DIVA), ("khadki", DIVA), ("miraj", DIVA), ("kolhapur", DIVA),
    ("solapur", DIVA), ("ratnagiri", DIVA), ("sawantwadi", DIVA),
    ("madgaon", DIVA), ("thokur", DIVA), ("mangalore", DIVA),
    ("ernakulam", DIVA), ("thiruvananthapuram", DIVA), ("tirunelveli", DIVA),
    ("chennai", DIVA), ("yesvantpur", DIVA), ("bengaluru", DIVA),
    ("mysore", DIVA), ("coimbatore", DIVA), ("tiruchchirappalli", DIVA),
    ("kazipet", DIVA), ("secunderabad", DIVA), ("kakinada", DIVA),
    ("velankanni", DIVA), ("tuticorin", DIVA), ("hubballi", DIVA),
    ("nanded", DIVA),
]


def region_of_code(code: str) -> str | None:
    return STATION_REGION.get((code or "").strip().upper())


def region_of_origin(name: str) -> str | None:
    """Region of the origin end of a train name like 'Surat - Panvel Express'."""
    head = (name or "").split("-")[0].strip().lower()
    for phrase, region in ORIGIN_REGION:
        if phrase in head:
            return region
    return None


def infer_corridors(name: str, dest_code: str, platform: int | None) -> tuple[str, str, str]:
    """(arrival_corridor, departure_corridor, confidence).

    Departure follows the destination. Arrival follows the origin in the name;
    when that is unknown we fall back on the platform, because PF6/PF7 are the
    east island that works the Diva branch - a train standing there with a
    NORTH departure must have come off the branch, and vice versa.
    """
    departure = region_of_code(dest_code)
    arrival = region_of_origin(name)
    confidence = "HIGH"

    if departure is None:
        # Unknown far end: the island platforms imply branch working.
        departure = DIVA if platform in (6, 7) else NORTH
        confidence = "LOW"

    if arrival is None:
        if platform in (6, 7):
            arrival = NORTH if departure == DIVA else DIVA
        else:
            arrival = SOUTH if departure == NORTH else NORTH
        confidence = "LOW" if confidence == "LOW" else "MEDIUM"

    if arrival == departure:
        # A service cannot arrive and leave the same way; trust the destination.
        arrival = SOUTH if departure == NORTH else NORTH
        confidence = "LOW"

    return arrival, departure, confidence
