"""
grouping.py -- deciding which candidate units fold into which other
entries, which affiliation(s) a unit shows under, and which merged
group each candidate belongs to.

Covers: passthrough/orphan-modifier detection, squadron-member/
container folding (compute_membership), garrison-spawner affiliation
inheritance, a squadron's fighter-member merge (merge_squadron_members,
including the _stat_multi_values/_squadron_members/fighter roster
aggregation), the "same ship" signature used to decide whether two
candidates merge into one card (variant_group_key_for), and small
shared text-cleanup helpers (dedupe_preserve_order,
clean_required_planets) used across rendering too.

Depends on xml_io (first_text, parse_affiliation_list, SPAWN_TAG_RE)
and model (hardpoint_names, unit_classes, NUMERIC_STAT_TAG_NAMES,
FILL_FROM_MEMBER_TAGS) for reading tags off a resolved unit; never
builds HTML itself.
"""

import re
from collections import Counter
import xml.etree.ElementTree as ET

from .xml_io import first_text, parse_affiliation_list, SPAWN_TAG_RE
from .model import hardpoint_names, unit_classes, NUMERIC_STAT_TAG_NAMES, FILL_FROM_MEMBER_TAGS



# ----------------------------------------------------------------------
# Structural grouping: fold fighters into squadrons, containers into
# squadrons, and hide pure "passthrough" inheritance stepping stones.
# ----------------------------------------------------------------------
def compute_referenced_parents(registry):
    """Every Name that at least one other registry entry points to via
    its own Variant_Of_Existing_Type tag. Shared by
    compute_passthrough_names (which hides such a node when it also has
    no Affiliation of its own) and compute_orphan_planet_modifiers
    (which only looks at nodes NOT in this set, since a node something
    else inherits from already gets its tags carried down normally)."""
    referenced_parents = set()
    for entry in registry.values():
        p = entry["elem"].find("Variant_Of_Existing_Type")
        if p is not None and (p.text or "").strip():
            referenced_parents.add((p.text or "").strip())
    return referenced_parents


def compute_passthrough_names(registry, exempt_names=frozenset()):
    """A unit is a passthrough (hidden) node if something else inherits
    from it via Variant_Of_Existing_Type AND either:
      - it doesn't declare its own Affiliation tag (catches abstract
        *_Upkeep / *_Required_Planets stepping stones), or
      - its name ends with "_Template" (catches template nodes that
        DO declare an Affiliation of their own -- often just copy-pasted
        from whatever they were templated off of -- but are still meant
        purely as a base for other units, not to be shown themselves).

    exempt_names is excluded from this rule entirely -- pass in every
    name that's ever referenced as a Squadron's Squadron_Units member.
    Fighter SpaceUnits never declare their own Affiliation (only their
    Squadron does), so without this exemption a fighter used as the
    base for another fighter variant (e.g. TIE_Interceptor being the
    parent of TIE_Interceptor_Royal) would be wrongly treated as an
    abstract template and hidden even though it's a real, flyable ship."""
    referenced_parents = compute_referenced_parents(registry)

    passthrough = set()
    for name in referenced_parents:
        if name in exempt_names:
            continue
        entry = registry.get(name)
        if entry is None:
            continue
        no_own_affiliation = entry["elem"].find("Affiliation") is None
        is_named_template = name.lower().endswith("_template")
        if no_own_affiliation or is_named_template:
            passthrough.add(name)
    return passthrough


# Tags a node is allowed to declare on its own (besides
# Variant_Of_Existing_Type) and still count as a "bare" modifier node
# for compute_orphan_planet_modifiers -- i.e. it adds nothing but a
# constraint, never its own identity (no Affiliation, no stat override,
# no garrison of its own).
BARE_PLANET_MODIFIER_ALLOWED_TAGS = {"Required_Planets"}


def compute_orphan_planet_modifiers(registry, resolved_all, referenced_parents):
    """Find nodes that declare nothing of their own except a
    Required_Planets tag, and that nothing else inherits from via
    Variant_Of_Existing_Type (so they're NOT already handled by
    compute_passthrough_names / normal inheritance).

    A mod author will sometimes factor a Required_Planets value out
    into its own node instead of repeating it on every real leaf unit
    that shares the same immediate parent (e.g. Pelta_Required_Planets,
    parented directly off Pelta_Template, sitting alongside
    Pelta_Rep -- also parented directly off Pelta_Template -- rather
    than being layered ABOVE Pelta_Rep in the inheritance chain the way
    Star_Destroyer_Torpedo_Required_Planets is). Nothing inherits from
    a node like that, it declares no Affiliation, no garrison, and no
    stat overrides -- so on its own it would render as an empty,
    unaffiliated orphan card with nothing but a planet list. It isn't a
    real, distinct ship; it's a constraint meant to apply to its real
    affiliated sibling(s).

    Returns {parent_name: [(bare_node_name, resolved_dict), ...]} so
    callers can look up, for any candidate unit, whether some bare
    modifier shares that candidate's own immediate
    Variant_Of_Existing_Type parent and should have its
    Required_Planets attached to that candidate (only when the
    candidate doesn't already declare its own -- see generate_html)."""
    by_parent = {}
    for name, entry in registry.items():
        if name in referenced_parents:
            continue  # something inherits from this -- already handled
        elem = entry["elem"]
        own_tags = {c.tag for c in elem if c.tag != "Variant_Of_Existing_Type"}
        if not own_tags or not own_tags <= BARE_PLANET_MODIFIER_ALLOWED_TAGS:
            continue
        parent_elem = elem.find("Variant_Of_Existing_Type")
        parent_name = (parent_elem.text or "").strip() if parent_elem is not None else ""
        if not parent_name:
            continue
        by_parent.setdefault(parent_name, []).append((name, resolved_all[name]))
    return by_parent


def compute_membership(resolved_all):
    """Returns (fold_names, all_member_names, container_used_names)
    using fully resolved data, so inherited (not just directly-
    declared) Squadron_Units / Create_Team_Type are captured too.

    fold_names: names to skip showing as their own top-level candidate
    because they're a member of SOME squadron's Squadron_Units --
    every member folds into its squadron's own combined card now,
    whether that squadron flies one uniform fighter type or several
    distinct ones (see merge_squadron_members / render_group_row,
    which render one hardpoint block and one gallery image per
    distinct member either way), and regardless of the member's own
    element tag (SpaceUnit, UniqueUnit, ...) -- see the tag-agnostic
    membership check in generate_html. A fighter is never shown as its
    own separate glossary entry once it's part of a squadron.

    all_member_names: EVERY name appearing in ANY squadron's
    Squadron_Units -- currently identical to fold_names by
    construction, but tracked as a separate return value because its
    purpose is different: it's used to exempt a fighter from the
    "passthrough" hidden-template rule (compute_passthrough_names). A
    fighter used as another fighter variant's Variant_Of_Existing_Type
    parent (e.g. Sentinel_CT parented on Lambda_CT) has no Affiliation
    of its own and would otherwise be wrongly treated as an abstract
    template -- that exemption is about inheritance, not about squadron
    folding, so it's kept as its own named concept even though today
    every folded name is also exempt."""
    fold_names = set()
    all_member_names = set()
    container_used = set()
    for resolved in resolved_all.values():
        if resolved.get("_tag") != "Squadron":
            continue
        su_raw = first_text(resolved, "Squadron_Units", "")
        tokens = [t.strip() for t in su_raw.split(",") if t.strip()]
        distinct = list(dict.fromkeys(tokens))
        all_member_names.update(distinct)
        fold_names.update(distinct)
        ctt = first_text(resolved, "Create_Team_Type", "")
        if ctt:
            container_used.add(ctt)
    return fold_names, all_member_names, container_used


def compute_garrison_spawners(resolved_all):
    """Returns {garrisoned_name: [spawner_affiliation, ...]} by scanning
    every Starting_Spawned_Units_Tech_N / Reserve_Spawned_Units_Tech_N
    tag anywhere in the registry and recording the (resolved)
    affiliation(s) -- see parse_affiliation_list -- of whichever
    unit/structure declares that spawn. A name can have multiple
    entries if different-affiliation spawners all garrison it, or if a
    single spawner itself declares more than one affiliation. Order is
    insertion order, duplicates removed."""
    spawners = {}
    for resolved in resolved_all.values():
        spawner_affs = parse_affiliation_list(first_text(resolved, "Affiliation", ""))
        for tag, els in resolved.items():
            if not isinstance(tag, str) or not SPAWN_TAG_RE.match(tag):
                continue
            for el in els:
                parts = [p.strip() for p in (el.text or "").split(",")]
                if not parts or not parts[0]:
                    continue
                garrisoned_name = parts[0]
                bucket = spawners.setdefault(garrisoned_name, [])
                for spawner_aff in spawner_affs:
                    if spawner_aff not in bucket:
                        bucket.append(spawner_aff)
    return spawners


NEUTRAL_AFFILIATION_VALUES = {"neutral"}


def get_affiliations(resolved, name):
    """Unified affiliation lookup: prefers an explicit override (set
    when a Neutral-affiliation unit is displayed under the affiliation
    of whatever spawns it as garrison -- see generate_html), otherwise
    falls back to the unit's own resolved Affiliation tag (split via
    parse_affiliation_list, since it can hold more than one faction).
    Returns an empty list if the unit declares no Affiliation of its
    own and no override applies -- affiliation is always read directly
    from the XML, never guessed from the unit's name."""
    if "_effective_affiliations" in resolved:
        return list(resolved["_effective_affiliations"])
    aff = first_text(resolved, "Affiliation", "")
    if aff:
        return parse_affiliation_list(aff)
    return []


def merge_squadron_members(merged, resolved_all):
    """Mutates/extends a Squadron's resolved dict copy with its
    members' stats, and computes squadron size.

    Squadron_Units can repeat one fighter Name many times (a plain
    N-ship squadron of a single fighter type) or list several distinct
    fighter Names (a mixed squadron, e.g. a fighter/bomber escort
    wing). Both are handled through the same path: every DISTINCT
    member is recorded in _squadron_members (a list of (name,
    resolved) pairs -- length 1 for a single-fighter-type squadron),
    which render_group_row uses to build one combined stat+hardpoint
    card per distinct member, plus one gallery image per member. A
    squadron member is never shown as its own separate glossary card
    (see compute_membership, which folds every squadron member
    regardless of composition).

    Per-ship combat stats (NUMERIC_STAT_TAGS -- Hull, Shields, AI
    Combat Power, Speed, etc.) are never pulled onto the squadron as a
    SINGLE value the way Cost/Population/Tech Level are (those are
    genuinely squadron-level figures this mod's Squadron elements
    declare directly) -- these vary per fighter, so each fighter's own
    figure is always shown on its own per-fighter card (see
    render_group_row, which reads them straight off each squadron
    member's own resolved dict). But when the squadron ITSELF doesn't
    declare a tag directly (the normal case for these), every distinct
    value found across its own distinct members is additionally
    collected into _stat_multi_values -- render_group_row's general
    Stats section reads this and joins the distinct values with commas
    for that stat-item, rather than leaving it flagged "Missing" just
    because the squadron never declares the tag itself. A uniform
    squadron (every member has the same value) still ends up showing
    just that one value, same as a single figure would. HardPoints is
    NOT handled this way and stays skipped entirely here: each
    member's hardpoints are always rendered as their own block, never
    merged into one squadron-level list or summarized as a stat-item.
    CategoryMask is handled as a union of all members' class tokens
    (deduped, pipe-joined) so Class stat/filtering still works.
    Text_ID/Encyclopedia_Text (used to derive a display name/
    description, not shown as a stat) fall back to the primary/most-
    common member rather than being comma-listed, since concatenating
    translation keys wouldn't produce anything meaningful."""
    su_raw = first_text(merged, "Squadron_Units", "")
    tokens = [t.strip() for t in su_raw.split(",") if t.strip()]
    if tokens:
        counts = Counter(tokens)
        primary_fighter = counts.most_common(1)[0][0]
        distinct_names = list(dict.fromkeys(tokens))
        distinct_members = [(n, resolved_all[n]) for n in distinct_names if n in resolved_all]

        merged["_squadron_members"] = distinct_members
        primary_resolved = resolved_all.get(primary_fighter)
        for t in FILL_FROM_MEMBER_TAGS:
            if t in merged:
                continue
            if t == "HardPoints" or t in NUMERIC_STAT_TAG_NAMES:
                continue  # shown per-member instead -- see render_group_row
            if t == "CategoryMask":
                cm_tokens = []
                for _n, r in distinct_members:
                    cm_tokens.extend(unit_classes(r))
                cm_tokens = list(dict.fromkeys(cm_tokens))
                if cm_tokens:
                    synthetic = ET.Element("CategoryMask")
                    synthetic.text = " | ".join(cm_tokens)
                    merged[t] = [synthetic]
                continue
            if t in ("Text_ID", "Encyclopedia_Text"):
                if primary_resolved and t in primary_resolved:
                    merged[t] = primary_resolved[t]
                continue

        # See the docstring above -- fills _stat_multi_values with every
        # distinct value declared by any of this squadron's own distinct
        # members, for whichever NUMERIC_STAT_TAGS tag the squadron
        # doesn't already declare a value for itself. Order preserved as
        # first-seen across distinct_members (itself already in
        # Squadron_Units token order).
        stat_multi_values = {}
        for xml_tag in NUMERIC_STAT_TAG_NAMES:
            if xml_tag in merged:
                continue
            values = []
            for _n, r in distinct_members:
                v = first_text(r, xml_tag, "")
                if v and v not in values:
                    values.append(v)
            if values:
                stat_multi_values[xml_tag] = values
        if stat_multi_values:
            merged["_stat_multi_values"] = stat_multi_values

        # Squadron_Units is a flat, comma-separated, order-sensitive list
        # of ship-type Names -- its raw token count is the squadron's
        # size, straight from the source data. This mod's source data
        # has at least one squadron with a Name accidentally repeated in
        # that list (e.g. "16 Ship Squadron" per that file's own comment,
        # but 17 raw tokens) -- deliberately NOT corrected here: the
        # glossary reflects what the mod's own data says, typo and all,
        # rather than second-guessing it against a different tag
        # (Squadron_Offsets) that happens to agree with the comment.
        merged["_squadron_size"] = sum(counts.values())
        merged["_squadron_composition"] = counts
        merged["_primary_fighter"] = primary_fighter

    ctt = first_text(merged, "Create_Team_Type", "")
    if ctt:
        container_resolved = resolved_all.get(ctt)
        if container_resolved:
            for t in FILL_FROM_MEMBER_TAGS:
                if t not in merged and t in container_resolved:
                    merged[t] = container_resolved[t]
    return merged


def garrison_signature(resolved):
    """Distinct set of unit/squadron names appearing across every
    Starting_Spawned_Units_Tech_N / Reserve_Spawned_Units_Tech_N tag on
    this entry, ignoring tier and count -- part of the "same ship"
    signature so that e.g. two similarly-statted capital ships flying
    genuinely different garrison complements are NOT treated as the
    same ship for merging purposes, even when their raw stats
    (health/shields/combat power/hardpoint count) happen to match."""
    names = set()
    for tag, els in resolved.items():
        if not isinstance(tag, str) or not SPAWN_TAG_RE.match(tag):
            continue
        for el in els:
            parts = [p.strip() for p in (el.text or "").split(",")]
            if parts and parts[0]:
                names.add(parts[0])
    return frozenset(names)


def unit_signature(resolved):
    """The general "same ship" signature: stats plus garrison
    composition. Used for every entry type -- see variant_group_key_for
    for why Squadrons compute a very similar but distinct signature
    (they fold in a fighter's stats via merge_squadron_members first,
    and don't generally have their own garrison)."""
    return (
        first_text(resolved, "Tactical_Health"),
        first_text(resolved, "Shield_Points"),
        first_text(resolved, "AI_Combat_Power"),
        len(hardpoint_names(resolved)),
        garrison_signature(resolved),
    )


def variant_group_key_for(candidate):
    """Grouping key used to decide which candidates get merged into one
    card. Every entry requires an identical Encyclopedia_Text
    description (the raw, untranslated list of localization keys --
    see resolve_description) AND a matching "same ship" signature --
    Tactical_Health, Shield_Points, AI_Combat_Power, hardpoint COUNT
    (not the exact hardpoint names -- see below), and garrison
    composition -- to line up before being grouped.

    Description equality is the primary signal because faction-reskin
    variants of the same ship in this mod's data are written with the
    same flavor text/tooltip key list, regardless of what each
    variant's own Name happens to be -- unlike a name-suffix
    convention, this doesn't depend on any particular mod's naming
    scheme, and it naturally keeps apart two units that just happen to
    share similar stats but are actually different ships (they'll have
    different descriptions). A unit with NO Encyclopedia_Text at all
    groups only against other description-less units with a matching
    signature -- if that turns out to over- or under-merge for a
    particular mod's data, --exclude-name-exact / --affiliation-
    overrides remain the manual escape hatches.

    Hardpoint NAMES are deliberately excluded from the signature (only
    the COUNT is checked, as a coarse sanity signal) since different
    faction variants can reference differently-NAMED but functionally
    identical hardpoints (e.g. separately palette-swapped turret
    definitions per faction).

    Affiliation is intentionally NOT part of this key -- every member's
    affiliation is read directly from its own resolved Affiliation tag
    (see get_affiliations) and the group's card lists every affiliation
    found among its members, so two same-description/same-stats
    variants with different Affiliation tags still merge into one card
    as intended.

    Which signature shape applies is decided by "_primary_fighter" in
    resolved (set by merge_squadron_members whenever Squadron_Units was
    actually present, regardless of the candidate's OWN declared XML
    tag) -- NOT by checking _tag == "Squadron" directly. A mod can (and
    this one does) declare a squadron-like entry as SpaceUnit/UniqueUnit
    that inherits Squadron_Units/Create_Team_Type from a Squadron-
    tagged ancestor via Variant_Of_Existing_Type; keying off _tag alone
    would put such an entry through the wrong (5-element, non-squadron)
    signature shape, which can never equal the 4-element squadron shape
    even when the underlying values would otherwise line up -- silently
    blocking it from ever merging with a genuine same-ship Squadron
    entry it should group with."""
    resolved = candidate["resolved"]
    description_key = first_text(resolved, "Encyclopedia_Text", "")
    if "_primary_fighter" in resolved:
        signature = (
            first_text(resolved, "Tactical_Health"),
            first_text(resolved, "Shield_Points"),
            first_text(resolved, "AI_Combat_Power"),
            len(hardpoint_names(resolved)),
        )
        return (description_key, signature)
    return (description_key, unit_signature(resolved))


def dedupe_preserve_order(items):
    """Case/whitespace-insensitive dedup that keeps first-seen casing and
    order -- used for affiliation lists, image caption labels, and
    Required_Planets tokens, all of which can otherwise show visible
    duplicates from minor formatting differences in the source XML."""
    seen = set()
    out = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


WW_SUFFIX_RE = re.compile(r"_WW$", re.IGNORECASE)
TRAILING_NUMBER_RE = re.compile(r"\d+$")


def clean_required_planets(raw):
    """Required_Planets values commonly list both a planet and one or
    more variant-location counterparts as separate tokens:
    - "_WW" (underground/secondary base), e.g. "Kuat, Kuat_WW"
    - a bare trailing number for a second landing zone on the same
      planet, e.g. "Tatooine, Tatooine2"
    Both get stripped and the results deduped, so e.g.
    "Tatooine, Tatooine2, Tatooine_WW, Tatooine2_WW" all collapse to
    one "Tatooine" entry."""
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    stripped = [WW_SUFFIX_RE.sub("", t) for t in tokens]
    stripped = [TRAILING_NUMBER_RE.sub("", t) for t in stripped]
    return dedupe_preserve_order(stripped)


def pick_primary_member(members):
    """Sort a merged group's members so index 0 is the representative
    one: prefer a member whose (effective) affiliation includes Empire,
    else the alphabetically first member (deterministic, not meaningful
    beyond that). Shared by render_group_row (which uses the primary
    member's stats/source/hardpoints as the row's representative
    numbers) and generate_html (which uses the primary member's
    _source to decide which file-group section the row belongs to)."""
    def sort_key(m):
        affs = get_affiliations(m["resolved"], m["name"])
        return (0 if "Empire" in affs else 1, m["name"])

    return sorted(members, key=sort_key)
