"""Continent of each speedrun.com country area (ISO 3166-1 alpha-2, lower case). speedrun.com has no continent
concept of its own, so a "Europe" location filter is expanded to these countries here.

Continents are addressed by the usual two-letter codes (EU, NA, ...). Four of them are also ISO country codes
on speedrun.com (AF Afghanistan, AS American Samoa, NA Namibia, SA Saudi Arabia); in the locations box the
continent wins, and those countries are reached by name. Antarctica is not a continent here: speedrun.com
already offers it as a country (aq), and grouping it with its uninhabited islands would only duplicate that."""

from __future__ import annotations

CONTINENT_CODES = {
    "AF": "Africa",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}
CONTINENTS = tuple(CONTINENT_CODES.values())
CODE_OF_CONTINENT = {name: code for code, name in CONTINENT_CODES.items()}

_AFRICA = """dz ao bj bw bf bi cm cv cf td km cg cd ci dj eg gq er sz et ga gm gh gn gw ke ls lr ly mg mw ml mr mu yt ma
mz na ne ng re rw sh st sn sc sl so za ss sd tz tg tn ug eh zm zw io"""
_ASIA = """af am az bh bd bt bn kh cn cx cc cy ge hk in id ir iq il jp jo kz kw kg la lb mo my mv mn mm np kp om pk
ps ph qa sa sg kr lk sy tw tj th tl tr tm ae uz vn ye"""
_EUROPE = """ax al ad at by be ba bg hr cz dk ee fo fi fr de gi gr gg va hu is ie im it je xk lv li lt lu mt md mc me
nl mk no pl pt ro ru sm rs sk si es sj se ch ua gb"""
_NORTH_AMERICA = """ai ag aw bs bb bz bm bq vg ca ky cr cu cw dm do sv gl gd gp gt ht hn jm mq mx ms ni pa pr bl kn lc
mf pm vc sx tt tc us vi um"""
_OCEANIA = "as au ck fj pf gu ki mh fm nr nc nz nu nf mp pw pg pn ws sb tk to tv vu wf"
_SOUTH_AMERICA = "ar bo br cl co ec fk gf gy py pe sr uy ve"

COUNTRY_CONTINENT: dict[str, str] = {}
for _name, _codes in (
    ("Africa", _AFRICA),
    ("Asia", _ASIA),
    ("Europe", _EUROPE),
    ("North America", _NORTH_AMERICA),
    ("Oceania", _OCEANIA),
    ("South America", _SOUTH_AMERICA),
):
    for _code in _codes.split():
        COUNTRY_CONTINENT[_code] = _name


def countries_in(continent: str) -> list[str]:
    return [code for code, name in COUNTRY_CONTINENT.items() if name == continent]


def match_continent(term: str) -> str | None:
    """The continent whose name or code equals `term`, case-insensitively."""
    t = term.strip().casefold()
    return CONTINENT_CODES.get(t.upper()) or next((name for name in CONTINENTS if t == name.casefold()), None)


def is_continent_code(area_id: str) -> bool:
    """True for the country ids that clash with a continent code (see the module docstring)."""
    return area_id.upper() in CONTINENT_CODES
