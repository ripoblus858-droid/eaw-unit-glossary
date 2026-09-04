"""
model.py -- pure per-unit derived data: reading a resolved unit's tags
and computing stats/labels from them, with no HTML and no cross-unit
merging logic.

Covers: hardpoint aggregate/per-hardpoint stats (range, fire rate,
damage, DPS, accuracy-by-class), the numeric stat tag tables
(NUMERIC_STAT_TAGS, FIGHTER_STAT_TAGS) used throughout rendering,
class/ability/hero-status extraction, and display-name/description
resolution (Text_ID translation, --display-name-overrides,
Encyclopedia_Text paragraph splitting).

Depends on xml_io for first_text (reading a single resolved unit's own
tags) but never reads files or touches the registry itself.
"""

import re

from .xml_io import first_text, SPAWN_TAG_RE



# ----------------------------------------------------------------------
# Derived stats
# ----------------------------------------------------------------------
def hardpoint_names(resolved):
    raw = first_text(resolved, "HardPoints", "")
    # tolerant split: some source files are missing a comma here or there
    return [x.strip() for x in re.split(r"[,\n\r]+", raw) if x.strip()]


def hardpoint_summary(names, hardpoints):
    """Aggregate counts by Type (see format_hardpoint_type_label for
    display) plus any hardpoint names referenced by a unit but not
    found in any parsed HardPoint definition. This is deliberately just
    the aggregate view now -- per-hardpoint range/fire-rate/damage/DPS
    and per-class accuracy live in single_hardpoint_stats /
    render_hardpoint_details instead, since summing range or fire rate
    across differently-purposed hardpoints (a point-defense laser and a
    capital-scale turbolaser on the same ship) produced a number that
    wasn't meaningfully "the ship's" range or fire rate."""
    counts = {}
    missing = []
    for n in names:
        hp = hardpoints.get(n)
        if hp is None:
            missing.append(n)
            continue
        t = (hp.findtext("Type") or "UNKNOWN").strip()
        counts[t] = counts.get(t, 0) + 1
    return counts, missing


# Order to display Fire_Inaccuracy_Distance classes in, matching the
# order they appear in this mod's own HardPoint files -- classes this
# hardpoint doesn't declare a value for are simply omitted, and any
# class name found in the XML but not listed here is appended
# afterward (in file order) rather than silently dropped.
INACCURACY_CLASS_ORDER = ["Fighter", "Bomber", "Gunship", "Transport",
                           "Corvette", "Frigate", "Capital", "SpaceStructure", "Super"]


def class_sort_rank(classes):
    """Sort rank for a row's class list (see unit_classes) using the
    same canonical ship-size progression INACCURACY_CLASS_ORDER already
    defines -- Fighter, Bomber, Gunship, ..., Super. The first token
    (case-insensitive) that matches one of those classes wins; other
    role/targeting tags this mod mixes into the same CategoryMask tag
    (AntiFighter, AntiCapital, NonCombatHero, ...) aren't ship-size
    classes and are skipped over. A row with none of its classes
    matching this vocabulary (or with no classes at all) gets a rank
    past every known class, so it sorts as its own group at the very
    end rather than being spliced in among classified rows under a
    guessed class."""
    lower_order = [c.lower() for c in INACCURACY_CLASS_ORDER]
    for c in classes:
        cl = c.strip().lower()
        if cl in lower_order:
            return lower_order.index(cl)
    return len(lower_order)


def hardpoint_inaccuracy_by_class(hp_elem):
    """Fire_Inaccuracy_Distance holds one '<Class>, <value>' pair per
    unit-size class this hardpoint's targeting spread differs for --
    NOT a hit percentage; it's a spatial miss-radius in the same
    distance units as Fire_Range_Distance; LOWER means more accurate.
    Returns an ordered list of (class, value) string pairs."""
    raw = {}
    for el in hp_elem.findall("Fire_Inaccuracy_Distance"):
        parts = [p.strip() for p in (el.text or "").split(",")]
        if len(parts) >= 2 and parts[0]:
            raw[parts[0]] = parts[1]
    ordered = [(c, raw[c]) for c in INACCURACY_CLASS_ORDER if c in raw]
    ordered += [(c, v) for c, v in raw.items() if c not in INACCURACY_CLASS_ORDER]
    return ordered


def single_hardpoint_stats(hp_elem, projectile_damage):
    """Per-hardpoint (not aggregate) range, fire rate, projectile
    damage/shot, and a raw DPS figure, plus its per-class inaccuracy
    list -- the detail shown for one row in the expandable hardpoints
    list (see render_hardpoint_details).

    Damage is looked up by this hardpoint's own Fire_Projectile_Type
    against the projectile_damage index built in build_registries;
    it's None (shown as "unknown") when no matching definition was
    found among the parsed files -- projectile damage lives in a
    separate XML this script only opportunistically indexes (see
    DAMAGE_TAG_CANDIDATES).

    "DPS (no misses)" is exactly what it says: damage/shot x shots/sec,
    ignoring Fire_Inaccuracy_Distance entirely. Converting an
    inaccuracy DISTANCE into an actual hit probability would need the
    target's hitbox/collision size, which isn't in these files, so
    rather than fabricate a per-class DPS number that isn't really
    grounded in the data, the raw (undiminished) DPS is shown once,
    and per-class inaccuracy distance is shown alongside it as its own
    figure -- larger inaccuracy against a class means this hardpoint's
    real effectiveness against that class is worse than raw DPS
    suggests, without claiming to know by how much.

    Fire rate is shots/sec sustained over a full fire cycle: a burst of
    Fire_Pulse_Count shots, each Fire_Pulse_Delay_Seconds apart, followed
    by a recharge of Fire_Min_Recharge_Seconds..Fire_Max_Recharge_Seconds
    (averaged, since the game rolls a random value in that range each
    cycle) before the next burst starts. The burst itself isn't
    instantaneous -- a multi-pulse weapon with a non-trivial per-pulse
    delay spends real time firing before it even starts recharging, so
    that time is folded into the cycle alongside the recharge, not
    ignored. A single-pulse weapon (or one with no delay tag) reduces to
    the same "shots / average recharge" figure this always used."""
    rng_text = hp_elem.findtext("Fire_Range_Distance")
    try:
        rng = float(rng_text) if rng_text else None
    except ValueError:
        rng = None

    pulses_text = hp_elem.findtext("Fire_Pulse_Count")
    pulse_delay_text = hp_elem.findtext("Fire_Pulse_Delay_Seconds")
    rmin_text = hp_elem.findtext("Fire_Min_Recharge_Seconds")
    rmax_text = hp_elem.findtext("Fire_Max_Recharge_Seconds")
    fire_rate = None
    try:
        pulses_f = float(pulses_text) if pulses_text else 1.0
        pulse_delay = float(pulse_delay_text) if pulse_delay_text else 0.0
        recharge = ((float(rmin_text) + float(rmax_text)) / 2.0
                    if rmin_text and rmax_text else None)
        if recharge and recharge > 0:
            # Time to fire the burst itself -- (pulses - 1) gaps of
            # Fire_Pulse_Delay_Seconds between pulses_f shots -- plus the
            # recharge before the next burst. max(..., 0) guards against
            # a negative Fire_Pulse_Count (this mod's own docs mention -1
            # meaning "never stop firing"), which shouldn't subtract
            # burst time that doesn't apply here.
            cycle_time = max(pulses_f - 1, 0) * pulse_delay + recharge
            if cycle_time > 0:
                fire_rate = pulses_f / cycle_time
    except (TypeError, ValueError):
        pass

    proj_name = (hp_elem.findtext("Fire_Projectile_Type") or "").strip()
    damage = projectile_damage.get(proj_name) if proj_name else None
    dps = damage * fire_rate if (damage is not None and fire_rate is not None) else None

    return {
        "range": rng,
        "fire_rate": fire_rate,
        "damage": damage,
        "dps": dps,
        "projectile": proj_name,
        "inaccuracy": hardpoint_inaccuracy_by_class(hp_elem),
    }


HARDPOINT_TYPE_PREFIX_RE = re.compile(r"^HARD_POINT_", re.IGNORECASE)


def format_hardpoint_type_label(raw_type):
    """HardPoint Type values are long, shouty XML constants like
    "HARD_POINT_WEAPON_ION_CANNON" -- every one of them shares the
    "HARD_POINT_" prefix, which adds nothing when they're already
    grouped under a "Hardpoints" heading, so it's stripped. The

    remainder is title-cased with underscores turned to spaces
    ("WEAPON_ION_CANNON" -> "Weapon Ion Cannon") purely to shorten and
    declutter the label for the narrow hardpoints column -- this is
    display-only and never affects the underlying counts/grouping,
    which still key off the raw string from hardpoint_summary."""
    stripped = HARDPOINT_TYPE_PREFIX_RE.sub("", raw_type)
    if not stripped:
        return raw_type
    return stripped.replace("_", " ").strip().title()


def spawned_units(resolved):
    """Returns {tier_num: [("Starting"|"Reserve", unit_name, count), ...]}"""
    out = {}
    for tag, els in resolved.items():
        m = SPAWN_TAG_RE.match(tag)
        if not m:
            continue
        kind, tier = m.group(1), m.group(2)
        for el in els:
            parts = [p.strip() for p in (el.text or "").split(",")]
            if len(parts) >= 2:
                out.setdefault(tier, []).append((kind, parts[0], parts[1]))
    return out


NUMERIC_STAT_TAGS = [
    ("Tactical_Health", "Hull"),
    ("Shield_Points", "Shields"),
    ("Shield_Refresh_Rate", "Shield Regen"),
    ("Build_Cost_Credits", "Cost"),
    ("Population_Value", "Pop"),
    ("Build_Time_Seconds", "Build Time"),
    ("AI_Combat_Power", "AI Combat Power"),
    ("Tech_Level", "Tech Level"),
    ("Max_Speed", "Speed"),
]
NUMERIC_STAT_TAG_NAMES = {t for t, _ in NUMERIC_STAT_TAGS}

# Stats shown on an individual fighter's own card within a squadron
# (see render_group_row's squadron_members branch) -- a DIFFERENT list
# from NUMERIC_STAT_TAGS above, since squadron-only concepts like Pop/
# Cost/Build Time/Tech Level don't apply to one fighter, and Max_Rate_
# Of_Turn (turn rate) is worth showing per-fighter but isn't part of
# the squadron-level general stats. Order here is display order, so
# Max_Rate_Of_Turn -- appended last -- shows at the end of a fighter's
# stat card.
FIGHTER_STAT_TAGS = [
    ("Tactical_Health", "Hull"),
    ("Shield_Points", "Shields"),
    ("Shield_Refresh_Rate", "Shield Regen"),
    ("AI_Combat_Power", "AI Combat Power"),
    ("Max_Speed", "Speed"),
    ("Max_Rate_Of_Turn", "Turn Rate"),
]

# Tags worth pulling from a fighter/Container into its Squadron card if
# the Squadron doesn't define them itself. CategoryMask is here so a
# Squadron card can be filtered by class (Fighter/Bomber/Corvette/...)
# even when the Squadron element itself doesn't declare its own
# CategoryMask -- that lives on the fighter/Container it flies instead.
# NOTE: for a squadron with recorded members (_squadron_members),
# merge_squadron_members deliberately skips the NUMERIC_STAT_TAG_NAMES
# entries in this list -- those are shown per-member instead of pulled
# up to the squadron. See merge_squadron_members / render_group_row.
FILL_FROM_MEMBER_TAGS = [t for t, _ in NUMERIC_STAT_TAGS] + [
    "HardPoints", "Text_ID", "Encyclopedia_Text", "CategoryMask"]


def unit_classes(resolved):
    """CategoryMask holds a '|'-separated list of tokens, e.g.
    "Frigate | AntiFighter" or "Capital | AntiCapital" -- the same
    ship-size vocabulary Fire_Inaccuracy_Distance's per-class values
    use (Fighter, Bomber, Gunship, Transport, Corvette, Frigate,
    Capital, SpaceStructure, Super), mixed in with role/targeting
    flags like AntiFighter/AntiCapital that this mod stuffs into the
    same tag. Returned as-is, not filtered down to a fixed list --
    this is purely data-driven from whatever tokens the mod actually
    uses. (Some tokens this mod uses, like "NonCombatHero", are
    excluded specifically from the Class FILTER's option list at
    generate_html-build time -- see CLASS_FILTER_EXCLUDE -- but still
    show up here and in the per-row Class stat, since that's genuine
    unit data.)"""
    raw = first_text(resolved, "CategoryMask", "")
    return [t.strip() for t in raw.split("|") if t.strip()]


def unit_ability_types(resolved):
    """Unit_Abilities_Data is a SubObjectList wrapper holding one or
    more <Unit_Ability><Type>...</Type>...</Unit_Ability> entries (e.g.
    <Type>HUNT</Type>) -- this pulls out just the Type value of each,
    in declaration order, skipping any entry with no Type. Unlike the
    resolved dict's other tags (which flatten to a plain text value via
    first_text), Unit_Abilities_Data's payload is nested one level
    deeper, so it needs its own extraction rather than first_text/
    all_texts. A unit can have more than one Unit_Abilities_Data block
    resolved onto it (in principle, e.g. after inheritance -- though
    resolve_unit's tag-overlay only keeps the LAST declared block per
    unit, same as any other tag) and each block can list more than one
    ability, so every block found is walked."""
    types = []
    for uad in resolved.get("Unit_Abilities_Data", []):
        for ability in uad.findall("Unit_Ability"):
            t = (ability.findtext("Type") or "").strip()
            if t:
                types.append(t)
    return types


# CategoryMask tokens excluded from the Class FILTER's option list
# specifically (not from unit_classes() itself, so they still appear
# in the per-row Class stat-item -- that's genuine unit data). Lower-
# cased for matching. "NonCombatHero" isn't a ship class at all, and
# is redundant now that there's a dedicated Unit Type (Regular/Hero)
# filter -- see is_hero_unit.
CLASS_FILTER_EXCLUDE = {"noncombathero"}


def is_hero_unit(resolved):
    """True if this unit counts as a "Hero" (unique, named) for the
    Unit Type filter: prefers an explicit <Is_Named_Hero>Yes/No</...>
    tag when the unit declares one (so a UniqueUnit that explicitly
    opts out is correctly treated as non-hero), and falls back to
    whether the unit's own block tag is UniqueUnit -- the element type
    this mod's hero units use -- when Is_Named_Hero isn't declared at
    all."""
    is_named_hero = first_text(resolved, "Is_Named_Hero", "")
    if is_named_hero:
        return is_named_hero.strip().lower() == "yes"
    return resolved.get("_tag") == "UniqueUnit"


def resolve_display_name(resolved, name, translations, display_name_overrides=None):
    """The unit's manual display-name override, if one is configured
    (see load_display_name_overrides / --display-name-overrides) --
    the HIGHEST-priority source, for the same reason affiliation
    overrides win outright: specifying a name by hand is itself a
    declaration that this is the name to show, regardless of what
    Text_ID/--translations would otherwise produce. Falls back to the
    unit's Text_ID, translated, if available; otherwise its raw XML
    Name (e.g. "TIE_Interceptor_Squadron")."""
    if display_name_overrides and name in display_name_overrides:
        return display_name_overrides[name]
    text_id = first_text(resolved, "Text_ID", "")
    if text_id and text_id in translations:
        return translations[text_id]
    return name


def has_translated_name(resolved, translations):
    """True if this unit's Text_ID resolves to a real translation.
    False covers both a missing Text_ID tag and a Text_ID present but
    absent from the loaded translation file -- either way, a unit with
    no resolvable in-game name is often leftover/debug/unused content
    (see --hide-untranslated)."""
    text_id = first_text(resolved, "Text_ID", "")
    return bool(text_id and text_id in translations)


def resolve_description(resolved, translations):
    """Encyclopedia_Text holds a whitespace-separated LIST of
    localization key tokens -- stat/ability blurb templates, hero
    ability callouts, and flavor text -- with literal "TEXT_LINE"
    tokens marking paragraph breaks in-game. Earlier this only kept
    keys ending in "_DESCRIPTION"/"_DESCRIPTIONN" on the theory that
    flavor text always used that suffix, but this mod isn't
    consistent: some units use the abbreviated "_DESC" instead (e.g.
    TEXT_TOOLTIP_HERO_TONFALK_181_DESC), and others end in no
    recognizable suffix at all (e.g. Pelta_Rep's own flavor key is
    just TEXT_TOOLTIP_PELTA_HSC) -- so a suffix guess silently dropped
    the description on both. Every key in the list is now translated
    and included, in order, split into paragraphs at each TEXT_LINE.
    A key with no matching translation is skipped rather than shown as
    its raw key name. Returns a list of paragraph strings (possibly
    empty)."""
    raw = first_text(resolved, "Encyclopedia_Text", "")
    paragraphs = []
    current = []
    for key in raw.split():
        if key == "TEXT_LINE":
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        text = translations.get(key, "").strip()
        if text:
            current.append(text)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs
