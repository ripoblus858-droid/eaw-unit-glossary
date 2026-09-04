"""
generate_glossary.py

Scans a set of directories containing Empire at War / Forces of Corruption
(Alamo engine) style XML files, resolves file-level mod overrides and
Variant_Of_Existing_Type inheritance chains, and generates a single
self-contained HTML glossary page listing unit stats, hardpoints,
garrison complements, and (optionally) icon images.

The page groups entries by the source XML file they came from (a
"file-group" section per file, sorted by relative path), and renders
each entry as one row rather than a grid card -- a row's attributes
(general stats, hardpoints, garrison) are laid out as side-by-side
columns, each of which itself wraps its individual stat items into
multiple columns as space allows. A merged faction group (see below)
counts as one row, filed under whichever member's source file is the
representative ("primary") member -- see pick_primary_member.

USAGE
    python3 generate_glossary.py --dirs BASE_DIR [MOD_DIR ...] \
        --output glossary.html [--images-dir images] [--image-ext png]

    --dirs takes directories in override order: files in a later
    directory replace files of the SAME RELATIVE PATH from an earlier
    directory, mirroring how the game itself layers a mod's Data folder
    on top of the base game / a base mod. This is a whole-file replace,
    not a per-tag merge -- if a later directory's file doesn't redeclare
    a unit that existed in the file it replaced, that unit is gone,
    exactly as the game would treat it.

MERGING BEHAVIOR
    Rather than one card per raw XML <SpaceUnit>/<Squadron>/<Container>
    definition, the script collapses related entries into one glossary
    card each:

    - A fighter SpaceUnit that appears inside some Squadron's
      Squadron_Units list is folded into that Squadron's card (its
      health/shields/hardpoints fill in whatever the Squadron doesn't
      define itself), rather than shown as its own entry. The number of
      fighters in the squadron is shown as "Squadron Size".
    - A Container referenced by a Squadron's Create_Team_Type is folded
      into that Squadron's card the same way, rather than shown
      separately.
    - A "passthrough" unit -- one that has no Affiliation of its own but
      that at least one other unit inherits from via
      Variant_Of_Existing_Type (e.g. an abstract *_Template, *_Upkeep,
      or *_Required_Planets stepping stone) -- is not shown as its own
      card. Whatever it adds (like a Required_Planets constraint) is
      still visible on its descendants, since normal inheritance already
      carries that tag down to them.
    - A "bare" Required_Planets modifier -- a node that declares nothing
      of its own besides a Required_Planets tag, and that nothing else
      inherits from via Variant_Of_Existing_Type (so it ISN'T caught by
      the passthrough rule above) -- is not shown as its own card
      either. Instead its Required_Planets value is attached to whatever
      other real (Affiliation-bearing) unit(s) share its immediate
      Variant_Of_Existing_Type parent and don't already declare their
      own Required_Planets. See compute_orphan_planet_modifiers.
    - Entries that share an identical Encyclopedia_Text description AND
      also share a matching "same ship" signature (stats + garrison
      composition -- see variant_group_key_for) are grouped into a
      single card. The card lists every affiliation found in the group
      -- read directly from each member's own Affiliation tag, never
      guessed -- and shows one image per distinct icon among the
      group's members, side by side, using the first (alphabetically,
      with an Empire member preferred if present) member's stats as the
      representative numbers.

NOTES / LIMITATIONS
    - Total weapon "damage output" can't be computed exactly from these
      files alone -- HardPoint definitions give fire rate, pulse count,
      and range, but actual per-shot damage lives in Projectile / Damage
      XML files this script doesn't know about. What IS computed: weapon
      hardpoint counts by type, combined shots/sec, and max range.
    - Icon_Name values are .TGA files, which browsers can't render. This
      script assumes you've converted/exported them elsewhere and writes
      <img> tags pointing at IMAGES_DIR/<icon-stem-lowercased>.<ext>,
      with a graceful fallback if the file 404s.
"""

import argparse
import os
import re
import shutil
import sys

from . import xml_io
from .xml_io import (
    collect_final_files, build_registries, index_projectile_files,
    load_affiliation_overrides, load_display_name_overrides,
    load_in_game_images, load_excluded_names, parse_translations,
    load_faction_logos, load_splash_config, load_unit_order,
    DEFAULT_EXCLUDED_NAMES_PATH,
)
from .render import resolve_images_base
from .html_output import generate_html


def find_used_image_filenames(pages, images_base):
    """Scan every generated page's HTML (see generate_html's returned
    (filename, html) list) for <img src="..."> references pointing
    into images_base, returning the set of referenced filenames --
    just the basename, lowercased (icon_src() always lowercases the
    stem it builds a src from, so this matches that convention rather
    than assuming the filesystem's own casing).

    Scanning the actual rendered output, rather than trying to
    intercept every icon_src() call site scattered across render.py
    (the main icon gallery, the garrison gallery, a squadron's
    fighter gallery, a lead squadron icon, ...), is deliberate: it's
    correct by construction for however many places an icon reference
    can appear, current or future, without needing to keep a second
    tracking mechanism in sync with render.py's own logic. images_base
    is matched as an exact prefix (regex-escaped, since an absolute
    --images-dir resolves to a file:// URI that can contain characters
    with regex meaning) -- it has to be the SAME string passed to
    generate_html for this to line up with what's actually in the
    HTML."""
    used = set()
    prefix = re.escape(images_base) + "/"
    pattern = re.compile(r"src=['\"]" + prefix + r"([^'\"]+)['\"]")
    for _filename, html in pages:
        for m in pattern.finditer(html):
            used.add(m.group(1).lower())
    return used



# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main():
    # __doc__ here resolves to THIS module's docstring (the block just
    # above the imports) -- it's the full original usage/merging-behavior
    # text, not a short cli.py-specific blurb, so that `--help` output
    # stays identical to the pre-split single-file script.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dirs", nargs="+", required=True,
                     help="Directories to scan, in override order (last wins on same relative path).")
    ap.add_argument("--output", default="glossary.html", help="Output HTML file path.")
    ap.add_argument("--images-dir", default="images",
                     help="Path (as used in the HTML's <img src>) where converted icon images live.")
    ap.add_argument("--image-ext", default="png", help="Extension of converted icon images (default: png).")
    ap.add_argument("--prune-unused-images", default=None, metavar="DEST_DIR",
                     help="After generating the page(s), scan the actual output for every "
                          "--images-dir file that got referenced anywhere (any gallery, on any "
                          "page), then MOVE (never delete) every file in --images-dir that "
                          "wasn't referenced into DEST_DIR (created if needed) -- so --images-dir "
                          "ends up holding only images your current roster actually uses, while "
                          "the rest stay recoverable in DEST_DIR rather than being destroyed. "
                          "Off by default. Combine with --prune-unused-images-dry-run to see "
                          "what would move without touching any files yet.")
    ap.add_argument("--prune-unused-images-dry-run", action="store_true",
                     help="With --prune-unused-images, print what WOULD be moved and where "
                          "without actually moving anything. No effect without "
                          "--prune-unused-images.")
    ap.add_argument("--image-size", type=int, default=110,
                     help="Display size, in pixels, of each icon image in the glossary "
                          "(default: 110). Applies uniformly to every image, including each "
                          "distinct fighter's icon in a squadron's gallery.")
    ap.add_argument("--image-background-color", default="#0d0f14",
                     help="Background color (any valid CSS color, e.g. a hex code like "
                          "#0d0f14 or a name like 'white') behind each icon image (default: "
                          "#0d0f14, a near-black matching the page theme). Raise this if an "
                          "icon's own background matches the default too closely to see it.")
    ap.add_argument("--include-no-image", action="store_true",
                     help="Include units with no Icon_Name / no image (excluded by default).")
    ap.add_argument("--exclude-name-contains", nargs="*", default=["Upgrade"],
                     help="Skip any unit whose Name contains one of these substrings, "
                          "case-insensitive (default: %(default)s). Pass --exclude-name-contains "
                          "with no values to disable.")
    ap.add_argument("--exclude-name-suffix", nargs="*", default=["_MP"],
                     help="Skip any unit whose Name ends with one of these suffixes "
                          "(default: %(default)s, i.e. skirmish-only variants). Pass "
                          "--exclude-name-suffix with no values to disable.")
    ap.add_argument("--exclude-name-exact", nargs="*", default=[],
                     help="Skip units with exactly these Name values -- a manual escape "
                          "hatch for cases the other heuristics get wrong (e.g. a squadron "
                          "that's only ever used as ship/structure garrison and never "
                          "independently player-buildable).")
    ap.add_argument("--translations", nargs="*", default=[],
                     help="Path(s) to TranslationManifest.xml-style localization file(s). "
                          "When given, each unit's Text_ID is resolved to its translated "
                          "display name, and any '..._DESCRIPTION'/'..._DESCRIPTIONN' key "
                          "found in its Encyclopedia_Text list is shown as flavor text. "
                          "Multiple files are layered in order (later overwrites earlier), "
                          "same as --dirs. Without this flag, cards fall back to the raw "
                          "XML Name and show no description, same as before.")
    ap.add_argument("--translation-language", default="ENGLISH",
                     help="Which <Translation Language=\"...\"> to use (default: ENGLISH).")
    ap.add_argument("--projectiles", nargs="*", default=[],
                     help="Path(s) to standalone projectile/damage XML file(s) (e.g. "
                          "projectiles.xml) for per-hardpoint Damage/Shot and DPS. Only "
                          "needed if such a file lives OUTSIDE the directories passed to "
                          "--dirs -- build_registries already opportunistically indexes any "
                          "matching definitions found while scanning --dirs, so --dirs alone "
                          "is enough when your projectile file(s) live somewhere underneath "
                          "one of those directories. Multiple files are layered in order "
                          "(later overwrites earlier on a name collision), and entries loaded "
                          "this way take priority over anything auto-indexed from --dirs with "
                          "the same Name.")
    ap.add_argument("--affiliation-overrides", nargs="*", default=[],
                     help="Path(s) to a manual affiliation-override file: one "
                          "'<exact unit Name>,<affiliation1>[,<affiliation2>,...]' pair per "
                          "line (# comments and blank lines OK). Forces the given unit(s)' "
                          "displayed/filtered affiliation(s) to exactly what's listed, "
                          "overriding its own Affiliation tag and every other heuristic "
                          "(garrison-spawner inheritance). Multiple files layer in order, "
                          "later overwriting earlier on a name collision.")
    ap.add_argument("--display-name-overrides", nargs="*", default=[],
                     help="Path(s) to a manual display-name-override file: one "
                          "'<exact unit Name>,<display name>' pair per line (# comments and "
                          "blank lines OK; only the FIRST comma on a line splits the unit Name "
                          "from the display text, so the display name itself may contain "
                          "commas). Forces the given unit(s)' or squadron(s)' shown title -- "
                          "in the card heading, gallery captions, and fighter-card headings "
                          "alike -- to exactly this text, overriding both its Text_ID's "
                          "translation (if --translations is given) and its raw XML Name (the "
                          "fallback when it isn't). Multiple files layer in order, later "
                          "overwriting earlier on a name collision.")
    ap.add_argument("--in-game-images-dir", default=None,
                     help="Path (as used in the HTML's <img src>, same rules as --images-dir) "
                          "where in-game screenshot images live. Only takes effect together with "
                          "--in-game-images -- without an association file there's nothing to "
                          "show, and this alone doesn't enable the feature.")
    ap.add_argument("--in-game-images", nargs="*", default=[],
                     help="Path(s) to a manual in-game-image association file: one "
                          "'<exact unit Name>,<image filename1>[,<image filename2>,...]' pair "
                          "per line (# comments and blank lines OK). Shows the given filename(s) "
                          "-- looked up under --in-game-images-dir exactly as given, extension "
                          "included -- as screenshot thumbnails (click one to view it full-size "
                          "in a page-wide modal) under that unit's/squadron's own icon gallery. "
                          "Multiple files layer in order, later OVERWRITING (not merging with) "
                          "an earlier file's list for the same unit Name.")
    ap.add_argument("--splash-config", default=None,
                     help="Path to a 'key=value' text file supplying the splash page's dynamic "
                          "title/description/author text -- recognized keys are 'title' "
                          "(also shown on every per-faction page, not just the splash; a "
                          "newline here becomes a real line break in the visible heading, "
                          "though the browser tab's own title text is always flattened to one "
                          "line), 'description' (the overview blurb; may span multiple lines/"
                          "paragraphs -- see load_splash_config for the multi-line format), and "
                          "'author' (a credits block, newlines preserved). description and "
                          "author accept raw HTML -- e.g. an <a href=\"...\">...</a> renders as "
                          "a real link -- title does not (newlines aside). Any key left unset "
                          "is simply omitted (title falls back to a default). # starts a "
                          "comment ONLY before any key= line has been seen.")
    ap.add_argument("--unit-order", default=None,
                     help="Path to a manual row-ordering file (one '[Faction Name]' section per "
                          "faction, each followed by '<source file>=<name1>, <name2>, ...' lines "
                          "-- see load_unit_order for the exact format). When this flag is NOT "
                          "given, generation uses the normal default order (ascending HP) as "
                          "always, and a file reflecting that exact order is written out (see "
                          "the console output for its path) -- hand-edit that file (reordering "
                          "the comma-separated names on a line reorders rows within that source "
                          "file; reordering the lines within a section reorders which source "
                          "file's block comes first) and pass it back in via this same flag on a "
                          "later run to use your custom order instead. A row or source file the "
                          "given file doesn't mention just falls back to the default order for "
                          "whatever's missing, appended after everything the file DOES specify.")
    ap.add_argument("--mod-icon", default=None,
                     help="Path to a single small image shown beside the title on the splash "
                          "page, as a visual identity for the mod (same path-resolution rules "
                          "as --images-dir). Omitted entirely if not given.")
    ap.add_argument("--gameplay-image", default=None,
                     help="Path to a single large illustrative image (e.g. a gameplay "
                          "screenshot) shown as a banner on the splash page, between the "
                          "description and the faction grid (same path-resolution rules as "
                          "--images-dir). Omitted entirely if not given.")
    ap.add_argument("--faction-logos-dir", default=None,
                     help="Path (as used in the HTML's <img src>, same rules as --images-dir) "
                          "where faction logo images live. Only takes effect together with "
                          "--faction-logos -- without an association file there's nothing to "
                          "show, and this alone doesn't enable the feature.")
    ap.add_argument("--faction-logos", nargs="*", default=[],
                     help="Path(s) to a manual faction-logo/display-name association file: one "
                          "'<faction name>,<logo image filename>[,<display name>]' pair per "
                          "line (# comments and blank lines OK; matched case-insensitively "
                          "against each faction's own affiliation value). Shows the given logo "
                          "-- looked up under --faction-logos-dir exactly as given, extension "
                          "included -- above that faction's name and entry count on the splash "
                          "page. The optional third field renames the NAME shown on that card "
                          "(e.g. 'CSA' -> 'Confederate Systems Alliance') without affecting "
                          "grouping or the per-faction page's own filename/content. Either the "
                          "logo filename or the display name may be left blank, but not both -- "
                          "'CSA,,Confederate Systems Alliance' renames with no logo. A faction "
                          "with no entry here at all just shows its own real name/count with no "
                          "logo. Multiple files layer in order, later overwriting earlier on a "
                          "name collision.")
    ap.add_argument("--hide-untranslated", action="store_true",
                     help="Skip any unit whose Text_ID doesn't resolve to a real translation "
                          "(missing Text_ID tag, or a Text_ID absent from the loaded "
                          "--translations file). Off by default. Only takes effect when "
                          "--translations is also given. Useful for cutting leftover/debug/"
                          "unused entries that were never given an in-game name.")
    ap.add_argument("--excluded-names-file", default=DEFAULT_EXCLUDED_NAMES_PATH,
                     help="Path to the list of exact Name values to always exclude (default: "
                          "excluded_names.txt next to this script). Checked alongside anything "
                          "passed via --exclude-name-exact -- use this file for entries you "
                          "always want gone regardless of which mod folders you point --dirs "
                          "at, --exclude-name-exact for a one-off run.")
    ap.add_argument("--squadron-icon-suppress-file", default=None,
                     help="Path to a list of exact squadron Name values (same one-per-line "
                          "format as --excluded-names-file) for which the squadron's own "
                          "Icon_Name should NOT be shown first in its fighter gallery, even "
                          "when it's genuinely distinct from every fighter's own icon. Off by "
                          "default -- only needed if that auto-detection picks the wrong "
                          "answer for a specific squadron.")
    args = ap.parse_args()

    # EXCLUDED_NAMES and SQUADRON_ICON_SUPPRESS_NAMES are mutable
    # module-level globals defined in xml_io.py (loaded once at import
    # time to an empty/default value); reassigning the module
    # ATTRIBUTE here (rather than a plain `global` rebind, which only
    # works within xml_io.py itself) is what makes every other module
    # that reads xml_io.EXCLUDED_NAMES / xml_io.SQUADRON_ICON_SUPPRESS_NAMES
    # (as an attribute lookup, not a `from ... import NAME` binding)
    # see the update -- see render.py/html_output.py's own qualified
    # reads of these two names.
    try:
        xml_io.EXCLUDED_NAMES = load_excluded_names(args.excluded_names_file)
    except OSError as e:
        print(f"! could not load excluded names from {args.excluded_names_file}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {len(xml_io.EXCLUDED_NAMES)} always-excluded name(s) from {args.excluded_names_file}")

    if args.squadron_icon_suppress_file:
        try:
            xml_io.SQUADRON_ICON_SUPPRESS_NAMES = load_excluded_names(args.squadron_icon_suppress_file)
        except OSError as e:
            print(f"! could not load squadron icon suppress list from "
                  f"{args.squadron_icon_suppress_file}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Loaded {len(xml_io.SQUADRON_ICON_SUPPRESS_NAMES)} squadron icon suppression(s) from "
              f"{args.squadron_icon_suppress_file}")

    print(f"Scanning {len(args.dirs)} director{'y' if len(args.dirs)==1 else 'ies'}...")
    final_files = collect_final_files(args.dirs)
    print(f"Resolved {len(final_files)} XML files after override merge.")

    registry, hardpoints_defs, projectile_damage = build_registries(final_files)
    print(f"Indexed {len(registry)} unit definitions, {len(hardpoints_defs)} hardpoint definitions, "
          f"and {len(projectile_damage)} projectile/damage definition(s) from --dirs.")

    if args.projectiles:
        extra_projectile_damage = index_projectile_files(args.projectiles)
        projectile_damage.update(extra_projectile_damage)
        print(f"Loaded {len(extra_projectile_damage)} projectile damage definition(s) from "
              f"--projectiles ({len(projectile_damage)} total).")

    affiliation_overrides = {}
    for path in args.affiliation_overrides:
        try:
            file_overrides = load_affiliation_overrides(path)
        except OSError as e:
            print(f"! could not load affiliation overrides from {path}: {e}", file=sys.stderr)
            sys.exit(1)
        affiliation_overrides.update(file_overrides)
        print(f"  loaded {len(file_overrides)} affiliation override(s) from {path}")
    if args.affiliation_overrides:
        print(f"Loaded {len(affiliation_overrides)} affiliation override(s) total.")

    display_name_overrides = {}
    for path in args.display_name_overrides:
        try:
            file_overrides = load_display_name_overrides(path)
        except OSError as e:
            print(f"! could not load display-name overrides from {path}: {e}", file=sys.stderr)
            sys.exit(1)
        display_name_overrides.update(file_overrides)
        print(f"  loaded {len(file_overrides)} display-name override(s) from {path}")
    if args.display_name_overrides:
        print(f"Loaded {len(display_name_overrides)} display-name override(s) total.")

    in_game_images = {}
    for path in args.in_game_images:
        try:
            file_images = load_in_game_images(path)
        except OSError as e:
            print(f"! could not load in-game-image associations from {path}: {e}", file=sys.stderr)
            sys.exit(1)
        in_game_images.update(file_images)
        print(f"  loaded {len(file_images)} in-game-image association(s) from {path}")
    if args.in_game_images:
        print(f"Loaded {len(in_game_images)} in-game-image association(s) total.")
    if in_game_images and not args.in_game_images_dir:
        print("! --in-game-images given without --in-game-images-dir -- in-game images will "
              "not be shown.", file=sys.stderr)

    faction_logos = {}
    for path in args.faction_logos:
        try:
            file_logos = load_faction_logos(path)
        except OSError as e:
            print(f"! could not load faction logos from {path}: {e}", file=sys.stderr)
            sys.exit(1)
        faction_logos.update(file_logos)
        print(f"  loaded {len(file_logos)} faction logo association(s) from {path}")
    if args.faction_logos:
        print(f"Loaded {len(faction_logos)} faction logo association(s) total.")
    if faction_logos and not args.faction_logos_dir:
        print("! --faction-logos given without --faction-logos-dir -- faction logos will "
              "not be shown.", file=sys.stderr)

    splash_config = {}
    if args.splash_config:
        try:
            splash_config = load_splash_config(args.splash_config)
        except OSError as e:
            print(f"! could not load splash config from {args.splash_config}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"  loaded splash config from {args.splash_config}: "
              f"{', '.join(sorted(splash_config)) or '(no recognized keys found)'}")

    # custom_unit_order stays None (not {}) when --unit-order wasn't
    # given at all -- that distinction is what generate_html uses to
    # decide whether to build a fresh order snapshot to write out
    # below, separate from whether the loaded map happens to be empty.
    custom_unit_order = None
    if args.unit_order:
        try:
            custom_unit_order = load_unit_order(args.unit_order)
        except OSError as e:
            print(f"! could not load unit order from {args.unit_order}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"  loaded unit order from {args.unit_order}: "
              f"{len(custom_unit_order)} faction(s) covered")

    # Both override files (and the in-game-image association file) are
    # keyed by the exact raw XML Name attribute -- NOT by whatever
    # currently displays for that unit (a translated Text_ID, or
    # another override) -- so a key copied from the in-game/translated
    # name instead of the source XML silently never matches anything
    # and has no effect. Flagging any key absent from the parsed
    # registry catches that mistake here, at generation time, rather
    # than leaving it to be noticed (or missed) in the rendered page.
    # The correct raw Name for any already-generated entry is always
    # shown on its card's .meta line (e.g.
    # "181st_Alpha_Defender_Squadron &middot; Squadron").
    for label, overrides in (
        ("affiliation", affiliation_overrides),
        ("display-name", display_name_overrides),
        ("in-game-image", in_game_images),
    ):
        unmatched = sorted(n for n in overrides if n not in registry)
        if unmatched:
            print(f"  ! {len(unmatched)} {label} override(s) did not match any parsed unit Name "
                  f"-- check for a typo, or whether the key should be the raw XML Name rather than "
                  f"a translated/display name (see each card's .meta line for the correct Name): "
                  f"{', '.join(unmatched)}", file=sys.stderr)

    translations = {}
    if args.translations:
        translations = parse_translations(args.translations, language=args.translation_language)
        print(f"Loaded {len(translations)} translation key(s) total.")

    images_base = resolve_images_base(args.images_dir)
    if images_base != args.images_dir:
        print(f"Resolved --images-dir to: {images_base}")

    in_game_images_base = None
    if args.in_game_images_dir:
        in_game_images_base = resolve_images_base(args.in_game_images_dir)
        if in_game_images_base != args.in_game_images_dir:
            print(f"Resolved --in-game-images-dir to: {in_game_images_base}")

    faction_logos_base = None
    if args.faction_logos_dir:
        faction_logos_base = resolve_images_base(args.faction_logos_dir)
        if faction_logos_base != args.faction_logos_dir:
            print(f"Resolved --faction-logos-dir to: {faction_logos_base}")

    # resolve_images_base is purely path-string manipulation (Windows
    # drive letter / POSIX absolute -> file:// URI, relative left
    # as-is) -- it doesn't care whether the path is a directory or a
    # single file, so it's reused as-is for these two standalone images.
    mod_icon_src = resolve_images_base(args.mod_icon) if args.mod_icon else None
    gameplay_image_src = resolve_images_base(args.gameplay_image) if args.gameplay_image else None

    # generate_html now returns one (filename, html) pair per page --
    # a splash/intro page (filename == the plain basename of --output)
    # plus one page per faction, each named by inserting the faction's
    # slug before --output's extension (e.g. "glossary.html" ->
    # "glossary_empire.html") -- see html_output._faction_filename.
    # Every page is written into the SAME directory as --output.
    pages, unit_order_snapshot_text = generate_html(
        registry, hardpoints_defs, images_base, args.image_ext,
        require_image=not args.include_no_image,
        exclude_name_substrings=args.exclude_name_contains,
        exclude_name_suffixes=args.exclude_name_suffix,
        exclude_name_exact=args.exclude_name_exact,
        translations=translations,
        hide_untranslated=args.hide_untranslated,
        projectile_damage=projectile_damage,
        affiliation_overrides=affiliation_overrides,
        image_size=args.image_size,
        image_bg_color=args.image_background_color,
        display_name_overrides=display_name_overrides,
        in_game_images_dir=in_game_images_base,
        in_game_images=in_game_images,
        output_basename=os.path.basename(args.output),
        title=splash_config.get("title"),
        description=splash_config.get("description"),
        author=splash_config.get("author"),
        mod_icon_src=mod_icon_src,
        gameplay_image_src=gameplay_image_src,
        faction_logos=faction_logos,
        faction_logos_dir=faction_logos_base,
        custom_unit_order=custom_unit_order,
    )

    output_dir = os.path.dirname(args.output) or "."
    for filename, html in pages:
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Wrote {path}")

    if unit_order_snapshot_text is not None:
        # Only built (see generate_html) when --unit-order wasn't given
        # at all -- the write path is derived from --output the same
        # way a per-faction page's filename is (see _faction_filename),
        # just with a fixed "unit_order" suffix instead of a faction
        # slug, so it lands right alongside the other output files.
        order_root, _order_ext = os.path.splitext(os.path.basename(args.output))
        order_filename = f"{order_root}_unit_order.txt"
        order_path = os.path.join(output_dir, order_filename)
        with open(order_path, "w", encoding="utf-8") as f:
            f.write(unit_order_snapshot_text)
        print(f"Wrote {order_path} -- edit it and pass it back via --unit-order to use a "
              f"custom row order.")

    if args.prune_unused_images:
        # Scan the pages we just wrote (in memory, already have them --
        # no need to re-read the files back off disk) for every
        # --images-dir filename actually referenced anywhere.
        used_filenames = find_used_image_filenames(pages, images_base)
        try:
            all_files = sorted(
                f for f in os.listdir(args.images_dir)
                if os.path.isfile(os.path.join(args.images_dir, f))
            )
        except OSError as e:
            print(f"! could not list --images-dir {args.images_dir} for pruning: {e}", file=sys.stderr)
            sys.exit(1)
        unused_files = [f for f in all_files if f.lower() not in used_filenames]

        if not unused_files:
            print(f"--prune-unused-images: all {len(all_files)} file(s) in {args.images_dir} "
                  f"are referenced -- nothing to move.")
        elif args.prune_unused_images_dry_run:
            print(f"--prune-unused-images (dry run): would move {len(unused_files)} of "
                  f"{len(all_files)} file(s) from {args.images_dir} to "
                  f"{args.prune_unused_images} (none moved):")
            for f in unused_files:
                print(f"  {f}")
        else:
            try:
                os.makedirs(args.prune_unused_images, exist_ok=True)
            except OSError as e:
                print(f"! could not create --prune-unused-images destination "
                      f"{args.prune_unused_images}: {e}", file=sys.stderr)
                sys.exit(1)
            moved = 0
            for f in unused_files:
                src_path = os.path.join(args.images_dir, f)
                dest_path = os.path.join(args.prune_unused_images, f)
                try:
                    shutil.move(src_path, dest_path)
                    moved += 1
                except OSError as e:
                    print(f"  ! could not move {f}: {e}", file=sys.stderr)
            print(f"--prune-unused-images: moved {moved} of {len(unused_files)} unused file(s) "
                  f"from {args.images_dir} to {args.prune_unused_images}; "
                  f"{len(all_files) - moved} file(s) remain in {args.images_dir}.")

