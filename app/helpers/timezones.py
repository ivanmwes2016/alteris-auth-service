"""
The timezone a school's day is measured in.

A school can set one explicitly (an override, or the registering device's zone
in countries that span several). Otherwise it comes from the school's country,
which is free text from the sign-up form, so it's matched by name. Countries
with several zones (the USA, Canada, Australia, ...) aren't listed: a school
there needs an explicit timezone, and until it has one the default applies.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

# Alteris starts in Africa; East Africa Time is the safest guess when nothing else is known.
DEFAULT_TIMEZONE = "Africa/Kampala"

COUNTRY_TIMEZONES: dict[str, str] = {
    # Africa
    "algeria": "Africa/Algiers",
    "angola": "Africa/Luanda",
    "benin": "Africa/Porto-Novo",
    "botswana": "Africa/Gaborone",
    "burkina faso": "Africa/Ouagadougou",
    "burundi": "Africa/Bujumbura",
    "cameroon": "Africa/Douala",
    "chad": "Africa/Ndjamena",
    "djibouti": "Africa/Djibouti",
    "egypt": "Africa/Cairo",
    "eritrea": "Africa/Asmara",
    "eswatini": "Africa/Mbabane",
    "ethiopia": "Africa/Addis_Ababa",
    "gabon": "Africa/Libreville",
    "gambia": "Africa/Banjul",
    "ghana": "Africa/Accra",
    "guinea": "Africa/Conakry",
    "ivory coast": "Africa/Abidjan",
    "cote d'ivoire": "Africa/Abidjan",
    "kenya": "Africa/Nairobi",
    "lesotho": "Africa/Maseru",
    "liberia": "Africa/Monrovia",
    "libya": "Africa/Tripoli",
    "madagascar": "Indian/Antananarivo",
    "malawi": "Africa/Blantyre",
    "mali": "Africa/Bamako",
    "mauritius": "Indian/Mauritius",
    "morocco": "Africa/Casablanca",
    "mozambique": "Africa/Maputo",
    "namibia": "Africa/Windhoek",
    "niger": "Africa/Niamey",
    "nigeria": "Africa/Lagos",
    "rwanda": "Africa/Kigali",
    "senegal": "Africa/Dakar",
    "sierra leone": "Africa/Freetown",
    "somalia": "Africa/Mogadishu",
    "south africa": "Africa/Johannesburg",
    "south sudan": "Africa/Juba",
    "sudan": "Africa/Khartoum",
    "tanzania": "Africa/Dar_es_Salaam",
    "togo": "Africa/Lome",
    "tunisia": "Africa/Tunis",
    "uganda": "Africa/Kampala",
    "zambia": "Africa/Lusaka",
    "zimbabwe": "Africa/Harare",
    # Europe
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "wales": "Europe/London",
    "northern ireland": "Europe/London",
    "ireland": "Europe/Dublin",
    "austria": "Europe/Vienna",
    "belgium": "Europe/Brussels",
    "croatia": "Europe/Zagreb",
    "czech republic": "Europe/Prague",
    "czechia": "Europe/Prague",
    "denmark": "Europe/Copenhagen",
    "finland": "Europe/Helsinki",
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "greece": "Europe/Athens",
    "hungary": "Europe/Budapest",
    "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "norway": "Europe/Oslo",
    "poland": "Europe/Warsaw",
    "portugal": "Europe/Lisbon",
    "romania": "Europe/Bucharest",
    "spain": "Europe/Madrid",
    "sweden": "Europe/Stockholm",
    "switzerland": "Europe/Zurich",
}


def is_valid_timezone(name: str) -> bool:
    return name in available_timezones()


def country_timezone(country: str | None) -> str | None:
    """The zone for a single-zone country, or None (unknown, or it spans several)."""
    if not country:
        return None
    return COUNTRY_TIMEZONES.get(country.strip().lower())


def school_timezone(*, explicit: str | None, country: str | None) -> ZoneInfo:
    """The school's own setting, else its country's zone, else East Africa Time."""
    for name in (explicit, country_timezone(country)):
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo(DEFAULT_TIMEZONE)
