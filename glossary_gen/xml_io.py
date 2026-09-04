"""
xml_io.py -- reading the mod's XML files and turning them into data.

Covers: locating/overriding files across --dirs, tolerant XML parsing
(safe_parse), the unit/hardpoint registry (build_registries) and its
Variant_Of_Existing_Type inheritance resolution (resolve_unit), the
projectile-damage index, translation manifest parsing, and every
plain-text parameter-file loader (excluded names, affiliation
overrides, display-name overrides, in-game-image associations).

Nothing in this module builds HTML or makes merging/display decisions
-- it only turns files on disk into Python data structures (the
registry, and {name: resolved-tag-dict} maps) that the rest of the
package consumes.
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from html import unescape


# ----------------------------------------------------------------------
# Tag names the script treats as "top-level unit definitions" vs.
# hardpoint definitions. Extend these if your mod uses other block types
# (e.g. UniqueUnit for heroes).
# ----------------------------------------------------------------------
UNIT_TAGS = {"SpaceUnit", "Squadron", "StarBase", "SpecialStructure",
             "Container", "UniqueUnit"}
HARDPOINT_TAG = "HardPoint"

# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------
XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")

# XML element names may not start with a digit (the spec's Name
# production requires a NameStartChar, and 0-9 isn't one), but this
# modding scene names unit-family wrapper tags after in-universe unit
# numbers/designations -- <181st>, <501st>, <212th>, etc. -- which
# ElementTree (correctly) refuses to parse as-is. These wrapper tags
# are purely cosmetic containers the script never reads by name (it
# walks every element via root.iter() and only looks at each
# descendant's OWN tag/Name attribute, never the wrapper's), so it's
# safe to prefix an underscore onto any digit-leading tag name before
# parsing -- <181st> becomes <_181st>, matching its closing tag the
# same way. Only matches right after a literal "<" or "</", so it
# can't misfire inside comments (which start "<!--", not a digit) or
# entity references (which start with "&", not "<").
TAG_DIGIT_START_RE = re.compile(r"(</?)([0-9][A-Za-z0-9_.:-]*)")


def _sanitize_digit_leading_tags(text):
    return TAG_DIGIT_START_RE.sub(lambda m: m.group(1) + "_" + m.group(2), text)


def safe_parse(path):
    """Parse an XML file, tolerating malformed <?xml ...?> declarations
    (this modding scene's files sometimes use version='1' etc., which
    trips up strict parsers) and digit-leading tag names (see
    TAG_DIGIT_START_RE)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"  ! could not read {path}: {e}", file=sys.stderr)
        return None

    text = XML_DECL_RE.sub('<?xml version="1.0" encoding="UTF-8"?>', text, count=1)
    text = _sanitize_digit_leading_tags(text)
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        print(f"  ! parse error in {path}: {e}", file=sys.stderr)
        return None


SPAWN_TAG_RE = re.compile(r"^(Starting|Reserve)_Spawned_Units_Tech_(\d+)$")

# ----------------------------------------------------------------------
# External config: always-excluded names.
#
# Used to be a hardcoded list here. It's now loaded from a plain text
# file shipped alongside this script (excluded_names.txt) so a mod-
# specific exclusion list can be edited -- or swapped out entirely for
# a different mod -- without touching this file. The loader function's
# docstring covers its file's format; the *.txt file itself carries the
# detailed reasoning that used to live in comments here so it travels
# with the data.
#
# Default location is next to this script; override with
# --excluded-names-file. At import time (so the module is still usable
# if imported directly, e.g. for testing, without going through main())
# a missing default file degrades to an empty set rather than crashing
# -- main() itself reloads from args.excluded_names_file and exits with
# a clear error if THAT can't be read, since that's the path where a
# silent empty fallback would be a real footgun.
# ----------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXCLUDED_NAMES_PATH = os.path.join(SCRIPT_DIR, "excluded_names.txt")


def load_excluded_names(path):
    """Load the EXCLUDED_NAMES set from a plain text file: one exact
    XML Name value per line. Lines starting with # are comments; blank
    lines are ignored. Raises OSError if the file can't be read."""
    names = set()
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(line)
    return names


def _load_excluded_names_or_empty(path):
    try:
        return load_excluded_names(path)
    except OSError:
        return set()


EXCLUDED_NAMES = _load_excluded_names_or_empty(DEFAULT_EXCLUDED_NAMES_PATH)

# Squadron Names for which the auto-detected "lead with the squadron's
# own icon" behavior (see render_group_row) should be suppressed, even
# when that icon is genuinely distinct from every fighter's own icon.
# No default file -- this is opt-in only, via --squadron-icon-suppress-
# file in main(); empty (no suppressions) until then. Uses the same
# one-exact-Name-per-line format as EXCLUDED_NAMES, so it's loaded with
# the same load_excluded_names() function rather than a new parser.
SQUADRON_ICON_SUPPRESS_NAMES = set()


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------

def collect_final_files(dirs):
    """Walk each directory (in override order) for *.xml files, keyed by
    path relative to that directory's root. A later directory's file
    replaces an earlier one at the SAME relative path (a whole-file
    replace, per the module docstring) -- but a Name collision between
    two DIFFERENT relative paths (e.g. the same unit redefined in a
    differently-named file in a later directory) isn't resolved here
    at all; that can only be resolved once build_registries actually
    parses Name attributes out of these files, so it has to process
    files in an order that still reflects --dirs priority. Returning a
    plain {rel: full} dict achieves that for free, IF the dict's
    iteration order is relied on rather than re-sorted afterward: this
    function inserts dir 1's files first, then dir 2's, etc., and
    Python dicts preserve a key's original insertion position even
    when its value is later overwritten (re-assigning final[rel] for
    an existing rel updates the value in place without moving it) --
    so a rel path first introduced by a later directory always lands,
    and stays, after every earlier directory's entries. See
    build_registries, which iterates this dict AS RETURNED (no
    re-sorting) for exactly this reason.

    Files within a single directory are sorted by relative path before
    being inserted -- os.walk()'s own file order isn't guaranteed by
    the filesystem, and without sorting here that non-determinism would
    carry into which of two same-named units defined in two different
    files IN THE SAME DIRECTORY happens to win, run to run."""
    final = {}
    for d in dirs:
        if not os.path.isdir(d):
            print(f"  ! not a directory, skipping: {d}", file=sys.stderr)
            continue
        dir_entries = []
        for root, _, files in os.walk(d):
            for fname in files:
                if not fname.lower().endswith(".xml"):
                    continue
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, d)
                dir_entries.append((rel, full))
        for rel, full in sorted(dir_entries):
            if rel in final:
                print(f"  override: {rel}  ({final[rel][1]} -> {d})")
            final[rel] = (full, d)
    return {rel: full for rel, (full, _src) in final.items()}


# Candidate tag names for a projectile/weapon-effect definition's
# per-shot damage value. Damage lives in a SEPARATE XML file from
# HardPoint definitions (referenced only by name, via a HardPoint's own
# Fire_Projectile_Type), which this script never parsed before -- this
# is a best-effort, tag-name-agnostic index: build_registries scans
# every element in every file for a Name attribute plus any ONE of
# these child tags (FIRST MATCH WINS, so order matters), regardless of
# what the element's own tag is called.
#
# "Projectile_Damage" is this mod's actual direct-hit damage tag (a
# <Projectile Name="..."> element, though the element's own tag name
# doesn't matter to this lookup -- only Name + one of these children
# does). "Projectile_Blast_Area_Damage" is checked second as a
# fallback for area-of-effect projectiles (e.g. a "_Barrage" rocket
# variant) that have no Projectile_Damage of their own at all -- their
# damage is entirely blast-radius damage instead. The remaining
# entries are speculative fallbacks for other mods/schemas that don't
# use either of the first two; if your mod's actual damage tag isn't
# in this list, add it.
DAMAGE_TAG_CANDIDATES = ["Projectile_Damage", "Projectile_Blast_Area_Damage",
                          "Damage", "Weapon_Damage", "Impact_Damage",
                          "Damage_Points", "Base_Damage"]


def extract_element_damage(elem):
    """Given any element, return the float value of the first tag in
    DAMAGE_TAG_CANDIDATES it declares (or None if it declares none /
    the value isn't parseable as a number). Shared by build_registries
    (which scans this over every non-unit/non-hardpoint element found
    under --dirs) and index_projectile_files (which scans it over
    elements in files passed via --projectiles, for projectile
    definitions that live outside the --dirs tree entirely)."""
    for dmg_tag in DAMAGE_TAG_CANDIDATES:
        dmg_text = elem.findtext(dmg_tag)
        if dmg_text:
            try:
                return float(dmg_text.strip())
            except ValueError:
                return None
    return None


def build_registries(final_files):
    """Parse every final XML file and index unit / hardpoint elements by
    Name attribute, plus an opportunistic {projectile_name: damage}
    index built from DAMAGE_TAG_CANDIDATES (see extract_element_damage)
    -- used to compute per-hardpoint damage/DPS in single_hardpoint_stats
    when a matching definition is found among the parsed files; left
    unset (None) for hardpoints whose Fire_Projectile_Type has no
    match. This only covers projectile files that happen to live
    somewhere under --dirs -- if yours live at a different directory
    level entirely, pass them via --projectiles / index_projectile_files
    instead (see main()).

    Iterates final_files in the order collect_final_files already built
    it in -- deliberately NOT re-sorted by relative path here. A Name
    collision between two units defined in DIFFERENT files (not the
    same relative path, so collect_final_files's own whole-file-replace
    override never even sees them as related) can only be resolved at
    this parsing step, by which registry[name]/hardpoints[name]/
    projectile_damage[name] assignment happens LAST -- and "last" has
    to mean "from the highest-priority --dirs entry", not "whichever
    file's path happens to sort alphabetically last". Re-sorting here
    would silently let an early --dirs entry's file win over a later
    one's intended override just because of filename spelling, with no
    warning at all (unlike the same-relative-path case, which prints
    an explicit override line)."""
    registry = {}     # name -> {"tag":..., "elem":..., "source":...}
    hardpoints = {}    # name -> Element
    projectile_damage = {}  # name -> float damage per shot

    for rel, full in final_files.items():
        root = safe_parse(full)
        if root is None:
            continue
        for elem in root.iter():
            name = elem.get("Name")
            if not name:
                continue
            if elem.tag in UNIT_TAGS:
                registry[name] = {"tag": elem.tag, "elem": elem, "source": rel}
            elif elem.tag == HARDPOINT_TAG:
                hardpoints[name] = elem
            else:
                dmg = extract_element_damage(elem)
                if dmg is not None:
                    projectile_damage[name] = dmg

    return registry, hardpoints, projectile_damage


def index_projectile_files(paths):
    """Parse one or more standalone projectile/damage XML files (e.g.
    a projectiles.xml living outside the --dirs tree -- see
    --projectiles) into the same {name: damage} shape build_registries
    produces from --dirs. Any element with a Name attribute and a tag
    matching DAMAGE_TAG_CANDIDATES is indexed, regardless of its own
    tag name (same rule as build_registries). Multiple paths are
    layered in order, later files overwriting earlier ones on a name
    collision -- same override philosophy as --dirs and --translations."""
    projectile_damage = {}
    for path in paths:
        root = safe_parse(path)
        if root is None:
            continue
        count_before = len(projectile_damage)
        for elem in root.iter():
            name = elem.get("Name")
            if not name:
                continue
            dmg = extract_element_damage(elem)
            if dmg is not None:
                projectile_damage[name] = dmg
        print(f"  loaded {len(projectile_damage) - count_before} projectile damage definition(s) from {path}")
    return projectile_damage


def load_affiliation_overrides(path):
    """Load manual affiliation overrides from a plain text file: one
    "<exact unit Name>,<affiliation1>[,<affiliation2>,...]" pair per
    line -- forces that unit's displayed/filtered affiliation(s) to
    exactly this list, regardless of what its own Affiliation tag
    says or what any other heuristic (garrison-spawner inheritance)
    would otherwise conclude. This is the HIGHEST-priority affiliation
    source (see generate_html) -- for the rare cases where every other
    signal (the tag itself, spawner) is wrong or missing for a specific
    unit and hand-fixing the source XML isn't practical (e.g. someone
    else's mod folder you don't want to edit). Lines starting with #
    are comments; blank lines are ignored. The affs portion is parsed
    with parse_affiliation_list, so it accepts more than one
    affiliation the same way an Affiliation tag can. Raises OSError if
    the file can't be read."""
    overrides = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                print(f"  ! {path}:{lineno}: expected '<unit name>,<affiliation>[,...]', "
                      f"skipping: {line!r}", file=sys.stderr)
                continue
            name, _, affs_raw = line.partition(",")
            name = name.strip()
            affs = parse_affiliation_list(affs_raw)
            if name and affs:
                overrides[name] = affs
            elif name:
                print(f"  ! {path}:{lineno}: no valid affiliation(s) after the name, skipping: {line!r}",
                      file=sys.stderr)
    return overrides


def load_display_name_overrides(path):
    """Load manual display-name overrides from a plain text file: one
    "<exact unit Name>,<display name>" pair per line -- forces that
    unit's shown title (in card headings, gallery captions, and
    fighter-card headings alike, since all of those go through
    resolve_display_name) to exactly this text, taking priority over
    both its Text_ID's translation (if --translations is given) and
    its raw XML Name (the fallback when it isn't). Useful for an
    internal codename that should read differently in the glossary, or
    for filling in a readable name entirely when no --translations
    file is available. Only the FIRST comma on a line splits the unit
    Name from the display text -- unlike --affiliation-overrides, the
    display-name portion is free to contain its own commas, e.g.
    "Foo,Reaper, Ace of the 7th" stores the display name "Reaper, Ace
    of the 7th" intact. Lines starting with # are comments; blank
    lines are ignored. Raises OSError if the file can't be read."""
    overrides = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                print(f"  ! {path}:{lineno}: expected '<unit name>,<display name>', "
                      f"skipping: {line!r}", file=sys.stderr)
                continue
            name, _, display_name = line.partition(",")
            name = name.strip()
            display_name = display_name.strip()
            if name and display_name:
                overrides[name] = display_name
            elif name:
                print(f"  ! {path}:{lineno}: no display name after the unit name, skipping: {line!r}",
                      file=sys.stderr)
    return overrides


def load_in_game_images(path):
    """Load manual in-game-image associations from a plain text file:
    one "<exact unit Name>,<image filename1>[,<image filename2>,...]"
    pair per line -- associates that unit with one or more in-game
    screenshot filenames, looked up under --in-game-images-dir exactly
    as given (extension included). Unlike icon_src()'s handling of
    Icon_Name, there's no stem-lowercasing or extension substitution
    here -- these filenames are entirely user-chosen (nothing in the
    XML drives them), so the file can mix extensions freely (a .jpg
    beside a .png) if that's what's actually on disk. Multiple files
    layer in order, later OVERWRITING (not merging with) an earlier
    file's list for the same unit Name -- same convention as
    --affiliation-overrides / --display-name-overrides. Lines starting
    with # are comments; blank lines are ignored. Raises OSError if
    the file can't be read."""
    images = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                print(f"  ! {path}:{lineno}: expected '<unit name>,<image filename>[,...]', "
                      f"skipping: {line!r}", file=sys.stderr)
                continue
            name, _, filenames_raw = line.partition(",")
            name = name.strip()
            filenames = [fn.strip() for fn in filenames_raw.split(",") if fn.strip()]
            if name and filenames:
                images[name] = filenames
            elif name:
                print(f"  ! {path}:{lineno}: no image filename(s) after the unit name, skipping: {line!r}",
                      file=sys.stderr)
    return images


def load_faction_logos(path):
    """Load manual faction-logo/display-name associations from a plain
    text file: one
    "<faction name>,<logo image filename>[,<display name>]"
    pair per line -- the logo filename (looked up under
    --faction-logos-dir exactly as given, extension included) shows as
    that faction's large logo on the splash page; the optional third
    field, if given, replaces the faction's NAME shown on that same
    card (e.g. renaming "CSA" to "Confederate Systems Alliance")
    without affecting anything else -- the actual affiliation value
    used for grouping/matching rows, and the per-faction page's own
    filename/content, are untouched; only the splash card's own label
    text changes. Either the logo filename OR the display name may be
    left blank (but not both) -- "CSA,,Confederate Systems Alliance"
    renames the card with no logo image; "Empire,empire_logo.png" shows
    a logo with the faction's own real name, unchanged.

    Keyed CASE-INSENSITIVELY (stored lowercased) since the splash page
    groups rows by lowercased affiliation the same way the rest of the
    tool does (see html_output.generate_html) -- "Empire", "empire",
    and "EMPIRE" all match the same faction. Only the FIRST TWO commas
    are structural (splitting name from filename from display name) --
    the display name itself may freely contain further commas, same
    convention as --display-name-overrides. Returns
    {lowercased faction name: (logo_filename_or_None, display_name_or_None)}.
    A faction with no entry here at all just shows its own real
    name/count with no logo, rather than a broken-image icon. Lines
    starting with # are comments; blank lines are ignored. Multiple
    files layer in order, later overwriting earlier on a name
    collision. Raises OSError if the file can't be read."""
    logos = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "," not in line:
                print(f"  ! {path}:{lineno}: expected "
                      f"'<faction name>,<logo filename>[,<display name>]', skipping: {line!r}",
                      file=sys.stderr)
                continue
            name, _, rest = line.partition(",")
            filename, _, display_name = rest.partition(",")
            name = name.strip()
            filename = filename.strip() or None
            display_name = display_name.strip() or None
            if name and (filename or display_name):
                logos[name.lower()] = (filename, display_name)
            elif name:
                print(f"  ! {path}:{lineno}: no logo filename or display name after the "
                      f"faction name, skipping: {line!r}", file=sys.stderr)
    return logos


def load_unit_order(path):
    """Load a manually-curated row-ordering file: one "[Faction Name]"
    section per faction, followed by one "<source file path>=<name1>,
    <name2>, ..." line per source XML file, e.g.:

        [Empire]
        Units/Space/consular.xml=Consular_R, Consular_E
        Units/Space/tie_fighters.xml=TIE_Fighter, TIE_Interceptor

        [Rebel]
        Units/Space/consular.xml=Consular_R
        Units/Space/x_wing.xml=X_Wing, Y_Wing

    A file exactly matching this format and reflecting the CURRENT
    default order is written automatically whenever --unit-order is
    NOT given (see html_output.generate_html) -- the intended workflow
    is: run once without --unit-order, hand-edit the generated file to
    taste (reordering the comma-separated names on a line reorders the
    ROWS within that source file; reordering the LINES within a
    section reorders which source file's whole block comes first), then
    pass that edited file back in via --unit-order on a later run.

    Each name is the row's own PRIMARY member's raw XML Name (the same
    value shown on that row's own .meta line) -- for a merged card,
    that's whichever single member's stats/hardpoints represent the
    whole card, NOT every raw Name folded into it. A name this file
    doesn't mention, or a source file this file doesn't mention at all
    (for a given faction), just falls back to the normal default order
    for whatever's missing, appended after everything this file DOES
    specify -- so the file never needs to be kept perfectly exhaustive
    as your roster changes; anything new just shows up in a reasonable
    place instead of being silently dropped or causing an error.

    A faction section this file never mentions at all uses the normal
    default order throughout, same as if no --unit-order were given
    for that faction specifically.

    Returns {faction_name_lowercased: [(source_path, [name1, name2,
    ...]), ...]}, preserving the order sections/lines/names appeared
    in the file. Lines starting with # are comments; blank lines are
    ignored. A line before any "[Faction]" header, or a "key=value"
    line with no "=" at all, is flagged and skipped. Raises OSError if
    the file can't be read."""
    order = {}
    current_faction = None
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current_faction = line[1:-1].strip().lower()
                order.setdefault(current_faction, [])
                continue
            if current_faction is None:
                print(f"  ! {path}:{lineno}: expected a '[Faction Name]' section header "
                      f"before any source-file line, skipping: {line!r}", file=sys.stderr)
                continue
            if "=" not in line:
                print(f"  ! {path}:{lineno}: expected '<source file>=<name1>, <name2>, ...', "
                      f"skipping: {line!r}", file=sys.stderr)
                continue
            source, _, names_raw = line.partition("=")
            source = source.strip()
            names = [n.strip() for n in names_raw.split(",") if n.strip()]
            if source and names:
                order[current_faction].append((source, names))
            elif source:
                print(f"  ! {path}:{lineno}: no unit name(s) after the source file, "
                      f"skipping: {line!r}", file=sys.stderr)
    return order


SPLASH_CONFIG_KEYS = {"title", "description", "author"}


def load_splash_config(path):
    """Load the splash page's dynamic title/description/author text
    from a simple "key=value" text file -- e.g.:

        title=My Mod Name
        A Subtitle On Its Own Line
        description=A one-paragraph pitch for the mod. See our
        <a href="https://example.com">website</a> for more.

        A second paragraph, still part of the description.
        author=Created by SomeModder, with help from the community.

    Only "title", "description", and "author" (see SPLASH_CONFIG_KEYS)
    are recognized keys; any other "key=..." line is flagged and
    skipped. Unlike every other parameter file in this tool, a value
    can span MULTIPLE lines (title included): once a "key=value" line
    starts a key, any following line that does NOT itself match
    "key=value" (including a blank line, which becomes a blank line in
    the value -- e.g. a paragraph break in "description") is appended
    to that same key's value, until the next recognized "key=" line or
    end of file. Lines starting with # are comments ONLY when no key is
    currently open (i.e. before any "key=" line, or right after one on
    their own line that happens to start with #) -- once inside a
    multi-line value, a line starting with # is just part of that text,
    since a description could legitimately want to mention a hashtag.

    description and author are rendered as RAW HTML, not escaped (see
    html_output.generate_html) -- so an <a href="...">...</a> written
    here becomes a real clickable link on the splash page. A literal
    "<" or "&" needs its own HTML entity if you want it to show up
    as-is, same as writing any other HTML by hand. title does NOT get
    this treatment (a newline in it becomes a real <br> in the visible
    heading, but any other markup is shown as literal text, and the
    browser tab's own <title> text is always flattened to a single
    line regardless, since that's a plain-text-only context).

    Returns {key: value} with trailing whitespace stripped from each
    value; a key never set is simply absent from the dict (callers
    should default it themselves). Raises OSError if the file can't
    be read."""
    config = {}
    current_key = None
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.rstrip("\n")
            stripped = line.strip()
            if current_key is None and (not stripped or stripped.startswith("#")):
                continue
            if "=" in line and line.split("=", 1)[0].strip() in SPLASH_CONFIG_KEYS:
                key, _, value = line.partition("=")
                current_key = key.strip()
                config[current_key] = value
                continue
            if current_key is None:
                print(f"  ! {path}:{lineno}: expected 'key=value' (key one of "
                      f"{sorted(SPLASH_CONFIG_KEYS)}), skipping: {line!r}", file=sys.stderr)
                continue
            config[current_key] += "\n" + line
    return {k: v.strip() for k, v in config.items()}


def parse_translations(paths, language="ENGLISH"):
    """Parse one or more TranslationManifest.xml-style localization files
    into {Key: translated_text}. Multiple paths are processed in order
    with later files' keys overwriting earlier ones (same override
    philosophy as --dirs), though normally you'll just pass one file.
    Only the given Language's <Translation> is kept.

    IMPORTANT: entity decoding is a two-step problem here. ElementTree
    decodes ordinary XML text nodes' entities (&apos; etc.) automatically,
    but these translations are wrapped in <![CDATA[...]]> -- and CDATA
    content is literal by design, so an entity like &apos; written
    inside one is NOT decoded by the parser; it comes through as the
    literal 6 characters "&apos;". html.unescape() cleans that up
    explicitly. Running it is safe even on text that was never inside
    CDATA (already-clean text has nothing matching an entity pattern for
    unescape() to act on)."""
    translations = {}
    for path in paths:
        root = safe_parse(path)
        if root is None:
            continue
        count_before = len(translations)
        for loc in root.iter("Localisation"):
            key = loc.get("Key")
            if not key:
                continue
            for trans in loc.iter("Translation"):
                if trans.get("Language") == language:
                    translations[key] = unescape(trans.text or "")
                    break
        print(f"  loaded {len(translations) - count_before} translation(s) from {path}")
    return translations


# ----------------------------------------------------------------------
# Inheritance resolution
# ----------------------------------------------------------------------
def resolve_unit(name, registry, cache, visiting=None):
    """Return {tag_name: [Element, ...]} representing this unit's fully
    inherited attribute set, walking Variant_Of_Existing_Type parents
    first and then overlaying this unit's own declared tags."""
    if name in cache:
        return cache[name]
    if visiting is None:
        visiting = set()
    if name in visiting:
        return {}
    visiting = visiting | {name}

    entry = registry.get(name)
    if entry is None:
        return {}
    elem = entry["elem"]

    parent_elem = elem.find("Variant_Of_Existing_Type")
    parent_name = (parent_elem.text or "").strip() if parent_elem is not None else ""

    result = {}
    if parent_name and parent_name in registry:
        result = dict(resolve_unit(parent_name, registry, cache, visiting))

    own_tags = {c.tag for c in elem if c.tag != "Variant_Of_Existing_Type"}
    for t in own_tags:
        result[t] = elem.findall(t)

    result["_Name"] = name
    result["_tag"] = entry["tag"]
    result["_source"] = entry["source"]
    cache[name] = result
    return result


def first_text(resolved, tag, default=""):
    els = resolved.get(tag)
    if not els:
        return default
    return (els[0].text or "").strip() or default


def all_texts(resolved, tag):
    return [(el.text or "").strip() for el in resolved.get(tag, [])]


def parse_affiliation_list(raw):
    """An Affiliation tag can hold a COMMA-SEPARATED list of factions
    rather than a single one (e.g. "Empire, Rebel, Underworld, CIS,
    Republic" on a shared hero unit any faction can build), and some
    values carry stray leading/trailing commas or whitespace from
    source-XML formatting quirks (e.g. ",Neutral"). Splitting and
    filtering out empty tokens here -- used everywhere an Affiliation
    tag's raw text needs to become a set of individual affiliations
    rather than display text -- keeps one messy multi-value tag from
    turning into a single illegible filter option/display value
    instead of its real individual faction values. Lives here (rather
    than in grouping.py, where every OTHER caller of it lives) because
    load_affiliation_overrides above needs it too, and grouping.py
    already depends on xml_io.py -- not the other way around."""
    return [t.strip() for t in raw.split(",") if t.strip()]
