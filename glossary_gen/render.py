"""
render.py -- turning a resolved unit (or merged group of units) into
the actual HTML for one glossary card/row.

Covers: the expandable per-hardpoint detail list, the icon gallery
helper shared by every image grid on the page (render_image_block),
and the big one -- render_group_row, which builds a single card's
complete markup: title, general stats, hardpoints, garrison (text and
image gallery), formation diagram, description, required planets/
structures, abilities, the per-fighter breakdown for a squadron, and
the in-game-screenshot click-to-expand gallery.

This is the module that ties xml_io/model/grouping/formation together
into markup; it doesn't decide WHICH candidates exist or how the page
around them is assembled -- that's html_output.py.
"""

import os
import re
from html import escape

from . import xml_io
from .xml_io import first_text
from .model import (
    hardpoint_names, hardpoint_summary, single_hardpoint_stats,
    format_hardpoint_type_label, spawned_units, resolve_display_name,
    resolve_description, unit_classes, unit_ability_types, is_hero_unit,
    NUMERIC_STAT_TAGS, FIGHTER_STAT_TAGS,
)
from .grouping import (
    get_affiliations, dedupe_preserve_order, clean_required_planets,
    pick_primary_member,
)
from .formation import (
    parse_squadron_offsets, render_formation_diagram, FORMATION_PLOT_SIZE_MULTIPLIER,
)



def render_hardpoint_details(hp_names, hardpoints_defs, projectile_damage):
    """Builds the collapsed-by-default <details> list of individual
    hardpoint cards (range, fire rate, damage/shot, raw DPS, and
    per-class inaccuracy) shown under the aggregate hardpoint counts.
    Hardpoints referenced by the unit but missing a parsed HardPoint
    definition are skipped here -- they're already surfaced by
    hardpoint_summary's "missing" note. Returns "" if there's nothing
    to show."""
    cards = []
    for n in hp_names:
        hp = hardpoints_defs.get(n)
        if hp is None:
            continue
        stats = single_hardpoint_stats(hp, projectile_damage)
        type_label = format_hardpoint_type_label((hp.findtext("Type") or "UNKNOWN").strip())

        items = []
        if stats["range"] is not None:
            items.append(
                f"<div class='stat-item'><span class='stat-label'>Range</span>"
                f"<span class='stat-value'>{stats['range']:g}</span></div>"
            )
        if stats["fire_rate"] is not None:
            items.append(
                f"<div class='stat-item'><span class='stat-label'>Fire Rate</span>"
                f"<span class='stat-value'>{stats['fire_rate']:.2f}/s</span></div>"
            )
        if stats["damage"] is not None:
            items.append(
                f"<div class='stat-item'><span class='stat-label'>Damage/Shot</span>"
                f"<span class='stat-value'>{stats['damage']:g}</span></div>"
            )
        else:
            items.append(
                "<div class='stat-item'><span class='stat-label'>Damage/Shot</span>"
                "<span class='stat-value'>unknown</span></div>"
            )
        if stats["dps"] is not None:
            items.append(
                f"<div class='stat-item'><span class='stat-label'>DPS (no misses)</span>"
                f"<span class='stat-value'>{stats['dps']:.1f}</span></div>"
            )

        inaccuracy_html = ""
        if stats["inaccuracy"]:
            # One flowing line -- "Fighter 25 · Bomber 20 · Capital 10" --
            # instead of a boxed stat-item per class (see .hp-accuracy-compact).
            inaccuracy_line = "<span class='acc-sep'>&middot;</span>".join(
                f"<span class='acc-class'>{escape(c)}</span> "
                f"<span class='acc-value'>{escape(v)}</span>"
                for c, v in stats["inaccuracy"]
            )
            inaccuracy_html = (
                f"<div class='hp-detail-section'><h4>Accuracy by Class "
                f"<span class='meta'>(inaccuracy distance -- lower is more accurate)</span></h4>"
                f"<div class='hp-accuracy-compact'>{inaccuracy_line}</div></div>"
            )

        cards.append(f"""
<div class="hp-detail-card">
  <div class="hp-detail-header">{escape(type_label)} <span class='meta'>{escape(n)}</span></div>
  <div class="row-stats">{''.join(items)}</div>
  {inaccuracy_html}
</div>""")

    if not cards:
        return ""
    return f"""
<details class="hp-details-toggle">
  <summary>Show {len(cards)} hardpoint{'s' if len(cards) != 1 else ''}</summary>
  <div class="hp-detail-list">{''.join(cards)}</div>
</details>"""


def render_hardpoint_block(hp_names, hardpoints_defs, projectile_damage, heading=None):
    """Renders one hardpoint aggregate+detail block: per-type counts, a
    Total hardpoints count, a missing-definition note, and the
    expandable per-hardpoint detail list (see render_hardpoint_details).
    Returns "" if hp_names is empty.

    Used both for a single ship's Hardpoints column (heading=None) and,
    for a squadron, once per distinct member fighter (heading=that
    member's raw Name) -- render_group_row stacks the blocks in the
    same Hardpoints column, one per fighter, rather than merging every
    member's weapons into one combined list. A member's own combat
    stats (Hull, Shields, etc.) are NOT part of this block -- those are
    rendered as a separate per-member card in the general stats column
    instead, at the same level as the squadron's own stats -- see
    render_group_row."""
    if not hp_names:
        return ""
    counts, missing = hardpoint_summary(hp_names, hardpoints_defs)
    hp_items = "".join(
        f"<div class='stat-item'><span class='stat-label'>{escape(format_hardpoint_type_label(t))}</span>"
        f"<span class='stat-value'>{c}</span></div>"
        for t, c in sorted(counts.items())
    )
    hp_extra = (
        f"<div class='stat-item'><span class='stat-label'>Total hardpoints</span>"
        f"<span class='stat-value'>{len(hp_names)}</span></div>"
    )
    missing_note = (
        f"<p class='note'>{len(missing)} hardpoint(s) referenced but not found in "
        f"any parsed HardPoint definition (damage/type unknown): "
        f"{escape(', '.join(missing))}</p>" if missing else ""
    )
    hp_details_html = render_hardpoint_details(hp_names, hardpoints_defs, projectile_damage)
    heading_html = f"<div class='hp-member-heading'>{escape(heading)}</div>" if heading else ""
    return (
        f"<div class='hp-member-block'>{heading_html}"
        f"<div class='row-stats'>{hp_items}{hp_extra}</div>{missing_note}{hp_details_html}</div>"
    )


# ----------------------------------------------------------------------
# HTML generation
# ----------------------------------------------------------------------
WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


def resolve_images_base(images_dir):
    """Turn --images-dir into something a browser can actually use as an
    <img src> base.

    - An absolute path (Windows drive-letter style like 'C:\\foo\\bar', or
      POSIX '/foo/bar') is converted into a proper file:// URI with correct
      slash direction and percent-encoding for spaces/special characters.
    - A relative path is just normalized to forward slashes and left
      relative -- the browser will resolve it relative to wherever
      glossary.html is opened from.
    """
    from urllib.parse import quote

    if WINDOWS_ABS_RE.match(images_dir):
        _drive, _, rest = images_dir.partition(images_dir[1])  # split at the ':'
        rest = rest.replace("\\", "/")
        if not rest.startswith("/"):
            rest = "/" + rest
        return "file:///" + images_dir[0].upper() + ":" + quote(rest, safe="/")
    elif images_dir.startswith("/"):
        return "file://" + quote(images_dir, safe="/")
    else:
        return images_dir.replace("\\", "/")


def icon_src(icon_name, images_base, image_ext):
    if not icon_name:
        return None
    stem = os.path.splitext(icon_name)[0].lower()
    return f"{images_base}/{stem}.{image_ext}"


def render_image_block(images_and_labels, fallback_text, lead_image_and_label=None):
    """images_and_labels: list of (src, label) or (src, label, sub_label)
    with distinct src values. label is the primary caption line (e.g. a
    unit's display name); sub_label, when present, is a second line
    shown beneath it in smaller/dimmer text (e.g. which affiliation(s)
    use that image) -- omit the third element, or pass "" / None for
    it, when there's nothing to show there. Renders one <img> if
    there's only one, or a side-by-side row with small captions if
    there's more than one.

    lead_image_and_label: optional (src, label) or (src, label,
    sub_label) shown ALONE, centered, on its own row above the rest --
    used for a squadron's own icon when it's distinct from its
    fighters' icons (see render_group_row), so it reads as "the
    squadron" first rather than blending into the same wrapped grid as
    the fighter roster below it."""
    if not images_and_labels and not lead_image_and_label:
        return f"<div class='img-fallback'>{escape(fallback_text)}</div>"

    def one_img(src, label, sub_label=None):
        cap = f"<div class='img-caption'>{escape(label)}</div>" if label else ""
        if sub_label:
            cap += f"<div class='img-caption img-caption-sub'>{escape(sub_label)}</div>"
        alt_text = label or fallback_text
        return (
            f"<div class='img-slot'>"
            f"<img src='{escape(src)}' alt='{escape(alt_text)}' "
            f"onerror=\"this.replaceWith(Object.assign(document.createElement('div'),"
            f"{{className:'img-fallback',textContent:'{escape(alt_text)}'}}))\">"
            f"{cap}</div>"
        )

    if lead_image_and_label:
        lead_html = f"<div class='img-lead-row'>{one_img(*lead_image_and_label)}</div>"
        if not images_and_labels:
            return lead_html
        rest_html = (
            one_img(*images_and_labels[0]) if len(images_and_labels) == 1
            else "<div class='img-row'>" + "".join(one_img(*t) for t in images_and_labels) + "</div>"
        )
        return lead_html + rest_html

    if len(images_and_labels) == 1:
        return one_img(*images_and_labels[0])
    return "<div class='img-row'>" + "".join(one_img(*t) for t in images_and_labels) + "</div>"


def render_group_row(members, hardpoints_defs, translations=None, projectile_damage=None,
                      images_dir=None, image_ext="png", image_size=110, display_name_overrides=None,
                      resolved_all=None, in_game_images_dir=None, in_game_images=None):
    """members: list of dicts {"name":..., "resolved":..., "icon_src":...}
    all belonging to the same variant group (see variant_group_key_for).
    Renders one row, laid out as an image, an identity block, and
    several attribute columns (general stats, hardpoints, garrison)
    side by side.

    images_dir/image_ext: needed to build a squadron's own gallery of
    per-member icons (see below) -- a squadron member never becomes
    its own top-level candidate, so it never gets an icon_src computed
    for it the way generate_html does for ordinary candidates; this
    function has to compute those itself, on demand, from each
    member's own resolved Icon_Name.

    resolved_all: the FULL {name: resolved} map generate_html built for
    every unit across every parsed XML file (not just this group's own
    members) -- needed to look up a garrisoned unit's/squadron's own
    icon for the garrison-image gallery, since a garrison spawn can
    reference a unit defined in a completely different source file.
    Garrison text rendering itself doesn't need this (spawned_units
    just reads the names straight off `resolved`); only resolving each
    garrisoned name's Icon_Name does. Pass None to skip that gallery
    entirely (falls back to the text-only Garrison Complement block).

    in_game_images_dir/in_game_images: manual (not XML-driven) in-game
    screenshot associations -- see load_in_game_images. in_game_images
    is the {name: [filename, ...]} map itself; every member's own name
    is looked up (unioned across the merged group, deduped by
    filename), not just the primary's, same as garrison/abilities/
    Required_Planets. Pass either as None/empty to skip the gallery
    entirely. Clicking a thumbnail opens it in a single shared modal
    overlay (see page_template.PAGE_TEMPLATE's #image-modal and
    page_script.openImageModal) rather than anything row-specific, so
    no per-row id is needed here for it.

    Returns {"html": str, "affiliations": [...], "classes": [...],
    "has_planets": bool} rather than a bare HTML string -- the three
    metadata fields are exactly what generate_html needs both to embed
    this row's data-affiliations/data-classes/data-has-planets
    attributes (already baked into "html") AND to accumulate the
    global set of filter options shown in the top-of-page filter bar,
    without generate_html having to re-derive them from the raw HTML
    or re-walk the members list itself."""
    translations = translations or {}
    projectile_damage = projectile_damage or {}
    members_sorted = pick_primary_member(members)
    primary = members_sorted[0]
    resolved = primary["resolved"]
    tag = resolved.get("_tag", "")
    source = resolved.get("_source", "")

    # Affiliations across the whole group.
    affiliations = []
    for m in members_sorted:
        affiliations.extend(get_affiliations(m["resolved"], m["name"]))
    affiliations = dedupe_preserve_order(affiliations)

    # A merged group of squadron VARIANTS (e.g. a Neutral base squadron
    # plus faction-specific overrides that only re-point Icon_Name/
    # Affiliation/Squadron_Units -- see variant_group_key_for, which
    # merges squadrons on description+signature the same as any other
    # unit) each independently resolved their OWN Squadron_Units into
    # their own "_squadron_members" list -- e.g. one variant's list
    # might be a single reskinned fighter while another's is the
    # inherited base fighter. Using only the primary member's own list
    # here would silently drop every other variant's roster/icons, so
    # every distinct member's list is combined into one, deduped by
    # fighter Name (first-seen -- i.e. the primary variant's own
    # fighters -- ordered first) so the same underlying fighter
    # referenced by more than one variant only appears once.
    #
    # fighter_affiliations tracks, separately, EVERY variant that flies
    # each fighter Name (not deduped/first-seen the way squadron_members
    # itself is) -- a fighter SpaceUnit essentially never declares an
    # Affiliation of its own in this mod (only its Squadron does -- see
    # compute_passthrough_names), so "which affiliation(s) crew this
    # fighter" has to come from whichever merged squadron variant(s)
    # actually list it in their own Squadron_Units, not from the
    # fighter's own (normally absent) Affiliation tag. Used below to
    # caption each fighter's gallery image the same way the non-squadron
    # branch capitons its images, for consistency.
    squadron_members = []
    _seen_squadron_member_names = set()
    fighter_affiliations = {}
    for m in members_sorted:
        variant_affs = get_affiliations(m["resolved"], m["name"]) or affiliations
        for member_name, member_resolved in (m["resolved"].get("_squadron_members") or []):
            fighter_affiliations.setdefault(member_name, []).extend(variant_affs)
            if member_name in _seen_squadron_member_names:
                continue
            _seen_squadron_member_names.add(member_name)
            squadron_members.append((member_name, member_resolved))

    if squadron_members:
        # A squadron's card shows a gallery of each distinct FIGHTER
        # flown by ANY variant in the merged group -- not the primary
        # variant's own generic Icon_Name -- captioned with that
        # fighter's own display name (and, underneath, which
        # affiliation(s) fly it -- see fighter_affiliations above), so
        # "which ships make up this squadron (across every merged
        # faction variant)" is answered by the image itself. Built with
        # the per-fighter affiliation sub-label unconditionally here --
        # it's stripped back off below if a distinct squadron-level
        # lead icon ends up taking over that job instead.
        images_and_labels = []
        member_srcs_seen = set()
        for member_name, member_resolved in squadron_members:
            member_icon = first_text(member_resolved, "Icon_Name", "")
            member_src = icon_src(member_icon, images_dir, image_ext) if (member_icon and images_dir) else None
            if member_src:
                member_srcs_seen.add(member_src)
                member_label = resolve_display_name(member_resolved, member_name, translations, display_name_overrides)
                member_affs = ", ".join(dedupe_preserve_order(fighter_affiliations.get(member_name, [])))
                images_and_labels.append((member_src, member_label, member_affs))

        # If the squadron declares its OWN Icon_Name and it's genuinely
        # distinct from every fighter's own icon (not a duplicate of one
        # of them -- some mods reuse a fighter's icon as a placeholder
        # squadron icon), lead the gallery with it, alone on its own
        # centered row (see render_image_block's lead_image_and_label):
        # some squadrons in this mod have a real, unique squadron-level
        # icon (e.g. a combined-formation button) worth showing first,
        # set apart from the individual fighters below it, not just
        # folded into the same wrapped grid as them. --squadron-icon-
        # suppress-file is the manual escape hatch for any specific
        # squadron where this heuristic gets it wrong.
        squadron_icon_raw = first_text(resolved, "Icon_Name", "")
        squadron_icon_src = (
            icon_src(squadron_icon_raw, images_dir, image_ext)
            if (squadron_icon_raw and images_dir) else None
        )
        lead_image_and_label = None
        if (squadron_icon_src and squadron_icon_src not in member_srcs_seen
                and primary["name"] not in xml_io.SQUADRON_ICON_SUPPRESS_NAMES):
            squadron_label = resolve_display_name(resolved, primary["name"], translations, display_name_overrides)
            squadron_affs = ", ".join(affiliations)
            lead_image_and_label = (squadron_icon_src, squadron_label, squadron_affs)
            # The lead icon now carries the affiliation line for the
            # whole squadron once, up top -- so the per-fighter images
            # below it drop their own affiliation sub-label (added
            # above) rather than repeating the same (or overlapping)
            # affiliation info under every fighter too.
            images_and_labels = [(src, label) for src, label, _sub in images_and_labels]

        img_html = render_image_block(images_and_labels, primary["name"], lead_image_and_label=lead_image_and_label)
    else:
        # Distinct images across the group, each captioned with the
        # (shortest, for the same reason display_label below picks the
        # shortest member label -- some variants carry extra translated
        # qualifiers) display name of whichever member(s) use that icon,
        # with which affiliation(s) use it as a second line underneath
        # -- matching the squadron gallery's name-then-affiliation
        # captioning above, for consistency between the two. A member
        # with no affiliation of its own (e.g. an orphaned
        # *_Required_Planets sibling with no Affiliation tag at all)
        # falls back to the GROUP's overall affiliations for that
        # second line rather than showing nothing there.
        icon_to_names = {}
        icon_to_affs = {}
        icon_order = []
        for m in members_sorted:
            if not m["icon_src"]:
                continue
            if m["icon_src"] not in icon_to_names:
                icon_to_names[m["icon_src"]] = []
                icon_to_affs[m["icon_src"]] = []
                icon_order.append(m["icon_src"])
            icon_to_names[m["icon_src"]].append(resolve_display_name(m["resolved"], m["name"], translations, display_name_overrides))
            icon_to_affs[m["icon_src"]].extend(get_affiliations(m["resolved"], m["name"]) or affiliations)
        images_and_labels = [
            (
                src,
                min(dedupe_preserve_order(icon_to_names[src]), key=len),
                ", ".join(dedupe_preserve_order(icon_to_affs[src])),
            )
            for src in icon_order
        ]
        img_html = render_image_block(images_and_labels, primary["name"])

    raw_name = primary["name"]
    # The displayed title picks the SHORTEST label among the group's
    # members -- but a manual override (see display_name_overrides)
    # always takes priority over any un-overridden member's label,
    # regardless of length: without this split, a deliberately-chosen
    # override on one member (e.g. "181st Alpha, Beta, Saber
    # Squadrons") could lose the shortest-wins comparison to some OTHER
    # member's un-overridden, auto-derived name (translated Text_ID or
    # raw XML Name) that just happens to be shorter (e.g. "Beta
    # Squadron") -- silently defeating the override's whole purpose.
    # When one or more members DO have an override, the shortest-wins
    # tie-break applies only among those overridden labels; the
    # un-overridden members' labels are excluded from consideration
    # entirely rather than being allowed to still win on length. Only
    # when NO member in the group has an override does this fall back
    # to the original shortest-among-everyone behavior.
    member_label_info = [
        (
            resolve_display_name(m["resolved"], m["name"], translations, display_name_overrides),
            bool(display_name_overrides and m["name"] in display_name_overrides),
        )
        for m in members_sorted
    ]
    override_labels = [label for label, is_override in member_label_info if is_override]
    display_label = min(override_labels or [label for label, _ in member_label_info], key=len)
    description = resolve_description(resolved, translations)

    # General stats become individual "stat-item" blocks that wrap into
    # as many columns as fit (see .row-stats grid CSS) -- this is the
    # "multiple columns for attributes within a row" layout, rather than
    # one long vertical key/value table.
    #
    # Every tag in NUMERIC_STAT_TAGS is always rendered now, even when
    # the unit doesn't declare it -- previously an empty value just
    # silently omitted that row, which made a genuine data gap (e.g. a
    # unit missing AI_Combat_Power) indistinguishable from the field
    # not applying, and fed a missing value into sorting as if it
    # simply didn't exist. Flagging it as "Missing" (styled via
    # .stat-value-missing) keeps the gap visible instead.
    stat_items = []
    build_cost = None
    hp = None
    # Aggregated across every member of the merged group, not just the
    # primary -- the same reason squadron_members/fighter_affiliations
    # are unioned above rather than read off `resolved` alone: a
    # different merged squadron variant can fly a different fighter
    # with its own distinct value for a tag the primary's own fighters
    # don't share, and that shouldn't be silently dropped just because
    # it came from a non-primary variant.
    stat_multi_values = {}
    for m in members_sorted:
        for xml_tag, values in (m["resolved"].get("_stat_multi_values") or {}).items():
            bucket = stat_multi_values.setdefault(xml_tag, [])
            for v in values:
                if v not in bucket:
                    bucket.append(v)
    for xml_tag, label in NUMERIC_STAT_TAGS:
        # The primary member's OWN directly-declared value (if it has
        # one) always wins outright over any aggregated multi-value --
        # a member's own _stat_multi_values only ever holds a tag it
        # does NOT declare directly (see merge_squadron_members), but a
        # DIFFERENT merged variant's own multi-values (aggregated above
        # regardless of which member contributed them) could otherwise
        # end up masking a value the primary genuinely does declare for
        # itself. Only fall back to the aggregated distinct-fighter
        # values when the primary has no direct value of its own.
        own_val = first_text(resolved, xml_tag)
        if own_val:
            val = own_val
        else:
            multi = stat_multi_values.get(xml_tag)
            val = ", ".join(multi) if multi else ""
        if val:
            stat_items.append(
                f"<div class='stat-item'><span class='stat-label'>{escape(label)}</span>"
                f"<span class='stat-value'>{escape(val)}</span></div>"
            )
        else:
            stat_items.append(
                f"<div class='stat-item'><span class='stat-label'>{escape(label)}</span>"
                f"<span class='stat-value stat-value-missing'>Missing</span></div>"
            )
        if xml_tag == "Build_Cost_Credits":
            try:
                build_cost = float(val) if val else None
            except ValueError:
                build_cost = None
        elif xml_tag == "Tactical_Health":
            # Hull's displayed value can be a comma-joined multi-value
            # string for a mixed squadron (see the val computation
            # above) rather than a single number -- unlike build_cost,
            # which just gives up (None) on that shape, HP sorting uses
            # the LARGEST parseable figure among however many are shown,
            # matching how a file-group's own position (see
            # html_output.generate_html) is keyed off its largest HP
            # entry too.
            hp = None
            for token in val.split(","):
                token = token.strip()
                try:
                    n = float(token)
                except ValueError:
                    continue
                if hp is None or n > hp:
                    hp = n

    if "_squadron_size" in resolved:
        stat_items.append(
            f"<div class='stat-item'><span class='stat-label'>Squadron Size</span>"
            f"<span class='stat-value'>{resolved['_squadron_size']}</span></div>"
        )

    if affiliations:
        stat_items.append(
            f"<div class='stat-item'><span class='stat-label'>Affiliation</span>"
            f"<span class='stat-value'>{escape(', '.join(affiliations))}</span></div>"
        )

    # Classes across the whole group (see unit_classes) -- shown as its
    # own stat-item and also exposed via data-classes on the row so the
    # top-of-page Class filter can match against it.
    classes = []
    for m in members_sorted:
        classes.extend(unit_classes(m["resolved"]))
    classes = dedupe_preserve_order(classes)
    if classes:
        stat_items.append(
            f"<div class='stat-item'><span class='stat-label'>Class</span>"
            f"<span class='stat-value'>{escape(', '.join(classes))}</span></div>"
        )

    # Unit ability names (Unit_Abilities_Data -> Unit_Ability -> Type --
    # see unit_ability_types), aggregated across every member of the
    # merged group rather than just the primary -- an ability list is
    # exactly the kind of per-member value that Required_Planets/
    # Required_Special_Structures turned out to need aggregating too,
    # so this follows the same members_sorted loop as Class right above
    # rather than reading once off the single `resolved` object.
    abilities = []
    for m in members_sorted:
        abilities.extend(unit_ability_types(m["resolved"]))
    abilities = dedupe_preserve_order(abilities)
    if abilities:
        stat_items.append(
            f"<div class='stat-item'><span class='stat-label'>Abilities</span>"
            f"<span class='stat-value'>{escape(', '.join(abilities))}</span></div>"
        )

    stats_html = f"<div class='row-stats'>{''.join(stat_items)}</div>" if stat_items else ""
    if stats_html:
        # A squadron's general stats fix to exactly 2 columns (however
        # many rows that takes) rather than the auto-fill column count a
        # standalone unit's general stats uses -- see .row-general
        # --squadron in the CSS. A squadron row doesn't need the base
        # .row-general class at all here -- that class's flex-sizing was
        # for being a direct flex sibling in .row-content, which a
        # squadron row no longer uses (see the grid layout below).
        general_class = "row-general--squadron" if squadron_members else "row-general"
        stats_html = f"<div class='{general_class}'>{stats_html}</div>"

    # A squadron's formation-shape diagram (built from its own
    # Squadron_Offsets -- see parse_squadron_offsets /
    # render_formation_diagram) renders at a fixed pixel size now (see
    # render_formation_diagram's docstring) -- .formation-box sizes to
    # that fixed content rather than stretching to match the title+
    # stats column beside it, so the diagram's size no longer depends
    # on how tall that column happens to be or reflows on window
    # resize. The "Formation" label sits above the plot row, inside the
    # box but outside the plotted square -- see .formation-label/
    # .formation-plot-row -- so it never risks overlapping a dot near
    # the square's edge the way an in-SVG corner caption could.
    formation_box_html = ""
    if squadron_members:
        formation_svg, formation_legend = render_formation_diagram(
            parse_squadron_offsets(resolved), size=image_size * FORMATION_PLOT_SIZE_MULTIPLIER)
        if formation_svg:
            legend_html = f"<div class='formation-legend-wrap'>{formation_legend}</div>" if formation_legend else ""
            formation_box_html = (
                f"<div class='formation-box'>"
                f"<div class='formation-label'>Formation</div>"
                f"<div class='formation-plot-row'>{formation_svg}{legend_html}</div>"
                f"</div>"
            )

    # Required_Planets can differ per group member (e.g. only some
    # affiliations of a merged unit need specific planets) -- show every
    # distinct value found across the group, captioned with which
    # affiliation(s) it applies to, rather than only the primary
    # member's value. Values are cleaned (_WW stripped, tokens deduped)
    # BEFORE being used as the grouping key, so e.g. "Kuat, Kuat_WW" from
    # one member and "Kuat" from another correctly collapse together.
    # As above, a member with no affiliation of its own falls back to
    # the group's overall affiliations rather than its raw name. Shown
    # as its own full-width line under the stats columns rather than a
    # stat-item, since a planet list can run long.
    planets_to_labels = {}
    planets_order = []
    for m in members_sorted:
        rp_raw = first_text(m["resolved"], "Required_Planets", "")
        if not rp_raw:
            continue
        rp_clean = ", ".join(clean_required_planets(rp_raw))
        affs = get_affiliations(m["resolved"], m["name"]) or affiliations or [m["name"]]
        if rp_clean not in planets_to_labels:
            planets_to_labels[rp_clean] = []
            planets_order.append(rp_clean)
        planets_to_labels[rp_clean].extend(affs)
    planets_lines = []
    for rp_clean in planets_order:
        labels = ", ".join(dedupe_preserve_order(planets_to_labels[rp_clean]))
        label_suffix = f" ({escape(labels)})" if len(members_sorted) > 1 else ""
        planets_lines.append(
            f"<div><span class='stat-label'>Required Planets{label_suffix}</span>: {escape(rp_clean)}</div>"
        )
    planets_html = f"<div class='row-planets'>{''.join(planets_lines)}</div>" if planets_lines else ""

    # Required_Special_Structures (e.g. a shipyard tier this unit needs
    # built first) can differ per group member the same way
    # Required_Planets can -- e.g. only one merged variant needs its
    # own faction's shipyard -- so it's aggregated the same way here:
    # every distinct value found across the group, captioned with which
    # affiliation(s) it applies to, rather than only the primary
    # member's value (previously read once off `resolved` via a plain
    # stat-item, which silently dropped every other merged variant's
    # own requirement). Not squadron-specific -- any unit type can
    # declare it, so this runs for both branches below same as
    # planets_html. Shown as its own full-width line rather than a
    # stat-item for the same reason as Required_Planets: a stat-item
    # assumes one shared value for the whole card, which no longer
    # holds once a value can vary per member.
    structures_to_labels = {}
    structures_order = []
    for m in members_sorted:
        ss_raw = first_text(m["resolved"], "Required_Special_Structures", "")
        if not ss_raw:
            continue
        ss_clean = ", ".join(s.strip() for s in ss_raw.split(",") if s.strip())
        if not ss_clean:
            continue
        affs = get_affiliations(m["resolved"], m["name"]) or affiliations or [m["name"]]
        if ss_clean not in structures_to_labels:
            structures_to_labels[ss_clean] = []
            structures_order.append(ss_clean)
        structures_to_labels[ss_clean].extend(affs)
    structures_lines = []
    for ss_clean in structures_order:
        labels = ", ".join(dedupe_preserve_order(structures_to_labels[ss_clean]))
        label_suffix = f" ({escape(labels)})" if len(members_sorted) > 1 else ""
        structures_lines.append(
            f"<div><span class='stat-label'>Special Structures{label_suffix}</span>: {escape(ss_clean)}</div>"
        )
    structures_html = f"<div class='row-structures'>{''.join(structures_lines)}</div>" if structures_lines else ""

    member_grid_html = ""
    if squadron_members:
        # A squadron's per-fighter breakdown is one self-contained
        # "fighter card" per distinct member -- the fighter's own name
        # as a single heading at the top (not repeated per column), a
        # Stats column (Hull, Shields, etc, read straight off that
        # member's own resolved dict; see merge_squadron_members, which
        # deliberately never pulls these up to the squadron level)
        # beside a Hardpoints column (that member's own weapons), with
        # the whole card visually grouped (background + border) so it
        # reads as one fighter's info, not two unrelated columns. Each
        # fighter's two columns only ever need to align with each
        # OTHER within that same card -- unlike a shared grid spanning
        # every fighter, there's no cross-fighter row to keep level, so
        # a simple flex pair per card is enough. The card heading pairs
        # each member's raw internal XML Name with its translated
        # in-game label (when one resolves and actually differs) --
        # the raw Name stays primary since distinct ships in a squadron
        # sometimes share the same translated title (e.g. a generic
        # "Combat Shuttle" name reused across variants) even though
        # they're different underlying units with different stats/
        # weapons, so the title alone wouldn't reliably distinguish the
        # cards; the in-game label is shown alongside it for
        # readability, not in place of it.
        cards = []
        for member_name, member_resolved in squadron_members:
            member_stat_items = []
            for xml_tag, label in FIGHTER_STAT_TAGS:
                val = first_text(member_resolved, xml_tag)
                if val:
                    member_stat_items.append(
                        f"<div class='stat-item'><span class='stat-label'>{escape(label)}</span>"
                        f"<span class='stat-value'>{escape(val)}</span></div>"
                    )
                else:
                    member_stat_items.append(
                        f"<div class='stat-item'><span class='stat-label'>{escape(label)}</span>"
                        f"<span class='stat-value stat-value-missing'>Missing</span></div>"
                    )
            member_hp_names = hardpoint_names(member_resolved)
            hp_inner = render_hardpoint_block(member_hp_names, hardpoints_defs, projectile_damage)

            columns = []
            if member_stat_items:
                columns.append(
                    f"<div class='fighter-card-col'><div class='hp-member-subheading'>Stats</div>"
                    f"<div class='row-stats'>{''.join(member_stat_items)}</div></div>"
                )
            if hp_inner:
                columns.append(
                    f"<div class='fighter-card-col'><div class='hp-member-subheading'>Hardpoints</div>"
                    f"{hp_inner}</div>"
                )
            if columns:
                member_label = resolve_display_name(member_resolved, member_name, translations, display_name_overrides)
                if member_label and member_label != member_name:
                    heading_inner = (
                        f"{escape(member_name)} "
                        f"<span class='fighter-card-label'>&mdash; {escape(member_label)}</span>"
                    )
                else:
                    heading_inner = escape(member_name)
                cards.append(
                    f"<div class='fighter-card'>"
                    f"<div class='fighter-card-heading'>{heading_inner}</div>"
                    f"<div class='fighter-card-columns'>{''.join(columns)}</div>"
                    f"</div>"
                )
        if cards:
            member_grid_html = f"<div class='row-members'>{''.join(cards)}</div>"
        hardpoints_html = ""
    else:
        hp_names = hardpoint_names(resolved)
        hp_block = render_hardpoint_block(hp_names, hardpoints_defs, projectile_damage)
        hardpoints_html = (
            f"<div class='row-hardpoints'><h3>Hardpoints</h3>{hp_block}</div>"
            if hp_block else ""
        )

    garrison_html = ""
    # Aggregated across every member of the merged group, not just the
    # primary -- the same reason squadron_members/Required_Planets/
    # Special Structures/Abilities are unioned above rather than read
    # off `resolved` alone. Garrison is a common way for exactly this
    # to matter: a hero variant merged with a plain sibling (matching
    # description/signature, so it merges even on a squadron -- see
    # variant_group_key_for, whose squadron-branch signature doesn't
    # include garrison at all) often carries its OWN garrison spawn
    # tags (or inherits them via Variant_Of_Existing_Type from an
    # ancestor in a different file) that the chosen primary member
    # doesn't have -- reading `resolved` alone would silently drop
    # that garrison from the merged card entirely. Tiers merge by
    # union (same tier key from two members combines its entries,
    # deduped on the exact (kind, name, count) tuple) rather than one
    # member's tier data overwriting another's.
    garrison = {}
    for m in members_sorted:
        for tier, items in spawned_units(m["resolved"]).items():
            bucket = garrison.setdefault(tier, [])
            for item in items:
                if item not in bucket:
                    bucket.append(item)
    if garrison:
        blocks = []
        for tier in sorted(garrison, key=int):
            items = garrison[tier]
            starting = [f"{c}x {escape(u)}" for k, u, c in items if k == "Starting"]
            reserve = [f"{c}x {escape(u)}" for k, u, c in items if k == "Reserve"]
            blocks.append(
                f"<div class='tier'><strong>Tech {tier}</strong>"
                f"<div>Starting: {', '.join(starting) or '&mdash;'}</div>"
                f"<div>Reserve max: {', '.join(reserve) or '&mdash;'}</div></div>"
            )
        garrison_html = f"<div class='row-garrison'><h3>Garrison Complement</h3>{''.join(blocks)}</div>"

    # A small image strip of the garrisoned units'/squadrons' own icons,
    # shown directly under the ship's own image(s) in .row-image --
    # "what does this garrison actually look like" answered visually,
    # alongside the text tier breakdown above. Every garrisoned Name is
    # looked up in resolved_all -- which already indexes every unit
    # across EVERY file passed to --dirs into one shared registry (the
    # same mechanism that makes cross-file Variant_Of_Existing_Type
    # inheritance work) -- so this resolves correctly even when the
    # garrisoned unit is defined in a completely different source XML
    # file than this one. Distinct RAW names only, in first-appearance
    # order across tiers, so the same garrisoned Name spawned in
    # multiple tiers doesn't get looked up twice; a name with no icon
    # (or not found at all -- e.g. a typo in the source data) is
    # skipped from the gallery and surfaced instead in a small note,
    # the same missing-data pattern render_hardpoint_block uses for a
    # hardpoint with no definition.
    #
    # The gallery is THEN also deduped by resolved DISPLAY LABEL (case/
    # whitespace-insensitive, same convention as dedupe_preserve_order),
    # on top of the raw-name dedup above -- a display-name override is
    # often used to represent several raw squadron variants (e.g.
    # 181st_Alpha/Beta/Saber_Defender_Squadron) as one merged card
    # elsewhere on the page, all sharing the identical override text;
    # without this second pass, a garrison that spawns more than one of
    # those raw variants would show the same icon+caption repeated once
    # per variant instead of once for the whole merged group.
    garrison_images_html = ""
    if garrison and resolved_all is not None:
        garrison_names_seen = set()
        garrison_names_order = []
        # Iterate tiers in numeric order (same as the sorted(garrison,
        # key=int) the text block above uses) rather than dict
        # iteration order -- spawned_units builds `garrison` off a SET
        # of matched tag names internally, so its insertion order isn't
        # reliably tier-numeric, and without sorting here the "first-
        # appearance" pick used to break dedup ties below could end up
        # arbitrary (e.g. a Tech 3 spawn's icon winning over Tech 1's).
        for tier in sorted(garrison, key=int):
            items = garrison[tier]
            for _kind, g_name, _count in items:
                if g_name not in garrison_names_seen:
                    garrison_names_seen.add(g_name)
                    garrison_names_order.append(g_name)
        garrison_images_and_labels = []
        seen_garrison_labels = set()
        missing_garrison_icons = []
        for g_name in garrison_names_order:
            g_resolved = resolved_all.get(g_name)
            if g_resolved is None:
                missing_garrison_icons.append(g_name)
                continue
            g_icon_raw = first_text(g_resolved, "Icon_Name", "")
            g_src = icon_src(g_icon_raw, images_dir, image_ext) if (g_icon_raw and images_dir) else None
            if g_src is None:
                missing_garrison_icons.append(g_name)
                continue
            g_label = resolve_display_name(g_resolved, g_name, translations, display_name_overrides)
            label_key = g_label.strip().lower()
            if label_key in seen_garrison_labels:
                continue
            seen_garrison_labels.add(label_key)
            garrison_images_and_labels.append((g_src, g_label))
        if garrison_images_and_labels or missing_garrison_icons:
            garrison_gallery = (
                render_image_block(garrison_images_and_labels, "Garrison")
                if garrison_images_and_labels else ""
            )
            missing_note = (
                f"<p class='note'>{len(missing_garrison_icons)} garrison entry(s) referenced but "
                f"no icon found: {escape(', '.join(missing_garrison_icons))}</p>"
                if missing_garrison_icons else ""
            )
            garrison_images_html = (
                f"<div class='row-garrison-images'>"
                f"<div class='row-garrison-images-label'>Garrison</div>"
                f"{garrison_gallery}{missing_note}</div>"
            )

    # In-game screenshot thumbnails (manual associations -- see
    # load_in_game_images) shown under the garrison gallery, in the
    # same .row-image column. Every member's own name is looked up
    # (not just the primary's), unioned and deduped by filename, same
    # reasoning as garrison/abilities/Required_Planets: a different
    # merged variant could have its own distinct screenshot(s) the
    # primary doesn't. Sized to roughly match the .row-image column's
    # own width (see .in-game-thumb img in styles.py) rather than a
    # small fixed thumbnail. Clicking one opens it in a single,
    # page-wide modal overlay (see openImageModal() in page_script.py
    # and #image-modal in page_template.py) -- one shared modal per
    # page, not one expand panel per row, since only one screenshot is
    # ever being viewed at a time regardless of which row it came from.
    in_game_images_html = ""
    if in_game_images:
        in_game_filenames = []
        seen_in_game_filenames = set()
        for m in members_sorted:
            for fname in (in_game_images.get(m["name"]) or []):
                if fname not in seen_in_game_filenames:
                    seen_in_game_filenames.add(fname)
                    in_game_filenames.append(fname)
        if in_game_filenames and in_game_images_dir:
            thumbs = "".join(
                f"<div class='in-game-thumb' onclick=\"openImageModal('{escape(in_game_images_dir)}/{escape(fname)}')\">"
                f"<img src='{escape(in_game_images_dir)}/{escape(fname)}' alt='In-game screenshot' loading='lazy'></div>"
                for fname in in_game_filenames
            )
            in_game_images_html = (
                f"<div class='row-in-game-images'>"
                f"<div class='row-in-game-images-label'>In-Game</div>"
                f"<div class='in-game-thumb-row'>{thumbs}</div></div>"
            )

    member_names_note = ""
    if len(members_sorted) > 1:
        other_names = [m["name"] for m in members_sorted if m["name"] != raw_name]
        member_names_note = (
            f"<div class='meta'>variants merged: {escape(', '.join(other_names))}</div>"
        )

    search_terms = " ".join(dedupe_preserve_order([display_label.lower(), raw_name.lower()]))
    # description is a list of paragraphs (see resolve_description) --
    # each becomes its own <p>, preserving the TEXT_LINE break points
    # from the source Encyclopedia_Text.
    description_html = "".join(f"<p class='row-description'>{escape(p)}</p>" for p in description)

    has_planets = bool(planets_lines)
    is_hero = is_hero_unit(resolved)
    # data-affiliations / data-classes / data-has-planets / data-unit-kind
    # drive the top-of-page filter bar (see generate_html / PAGE_TEMPLATE
    # JS) -- lowercased, space-separated tokens so the filter checkboxes'
    # values (also lowercased) can be matched with a simple split+
    # includes check, no case-folding needed at filter time.
    aff_attr = " ".join(sorted({a.lower() for a in affiliations}))
    class_attr = " ".join(sorted({c.lower() for c in classes}))
    planets_attr = "yes" if has_planets else "no"
    unit_kind_attr = "hero" if is_hero else "regular"

    html = f"""
<div class="row row--squadron" data-name="{escape(search_terms)}" data-affiliations="{escape(aff_attr)}" data-classes="{escape(class_attr)}" data-has-planets="{planets_attr}" data-unit-kind="{unit_kind_attr}">
  <div class="row-image">{img_html}{garrison_images_html}{in_game_images_html}</div>
  <div class="row-squadron-body">
    <div class="row-grid-middle">
      <div class="row-header">
        <h2>{escape(display_label)}</h2>
        <div class="meta">{escape(raw_name)} &middot; {escape(tag)}</div>
        {member_names_note}
      </div>
      {stats_html}
      {garrison_html}
    </div>
    <div class="row-grid-formation">{formation_box_html}</div>
    <div class="row-grid-lower">
      {description_html}
      {planets_html}
      {structures_html}
      {member_grid_html}
    </div>
  </div>
</div>""" if squadron_members else f"""
<div class="row" data-name="{escape(search_terms)}" data-affiliations="{escape(aff_attr)}" data-classes="{escape(class_attr)}" data-has-planets="{planets_attr}" data-unit-kind="{unit_kind_attr}">
  <div class="row-image">{img_html}{garrison_images_html}{in_game_images_html}</div>
  <div class="row-body">
    <div class="row-header">
      <h2>{escape(display_label)}</h2>
      <div class="meta">{escape(raw_name)} &middot; {escape(tag)}</div>
      {member_names_note}
    </div>
    <div class="row-content">
      {stats_html}
      {hardpoints_html}
      {garrison_html}
    </div>
    {description_html}
    {planets_html}
    {structures_html}
  </div>
</div>"""

    return {
        "html": html,
        "affiliations": affiliations,
        "classes": classes,
        "has_planets": has_planets,
        "build_cost": build_cost,
        "hp": hp,
        "sort_name": display_label.lower(),
        "is_hero": is_hero,
        "raw_name": raw_name,
    }
