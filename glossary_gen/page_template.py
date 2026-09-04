"""
page_template.py -- the glossary page's overall HTML skeleton.

Combines the pre-rendered CSS (PAGE_CSS.format(...), from styles.py)
and the JS (PAGE_SCRIPT, from page_script.py) with the per-generation
{filters}/{count}/{groups} content built by html_output.generate_html.
{title} (plain, flattened -- used in <title> in the document head,
which can't contain markup) and {title_html} (the same text but with
any embedded newline turned into <br>, used in the visible <h1>) are
both shared by both templates below (see load_splash_config) -- every
generated page, not just the splash, shows the mod's own title rather
than a hardcoded one.

Also holds SPLASH_TEMPLATE, the intro page: mod icon/title/author
header, an overview description, an illustrative gameplay image, and
a grid of per-faction logo cards linking to each faction's own page --
see below.
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{styles}</style>
</head>
<body>
<h1>{title_html}</h1>
<h2>Unit Glossary</h2>
<div class="controls">
  <input id="search" type="text" placeholder="Search by name...">
</div>
{filters}
<div class="count">{count} entries</div>
<div id="groups">
{groups}
</div>
<div id="image-modal" class="image-modal-overlay">
  <button type="button" class="image-modal-close" onclick="closeImageModal()">Close &times;</button>
  <img id="image-modal-img" class="image-modal-img" src="" alt="In-game screenshot">
</div>
<script>{script}</script>
</body>
</html>
"""

# The intro/splash page (see html_output.generate_html). Every slot
# below is optional and simply omitted (as an empty string) when the
# corresponding --mod-icon/--gameplay-image/--splash-config value/
# faction logo isn't supplied -- {mod_icon}/{author}/{description}/
# {gameplay_image} are each pre-built as either a complete HTML
# fragment or "" by generate_html, so this template never needs its
# own conditionals. Reuses PAGE_CSS (same {styles} slot/rendering as
# PAGE_TEMPLATE above) for a consistent theme, but has no search box,
# filter bar, or JS at all, since there's nothing here to filter.
SPLASH_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{styles}</style>
</head>
<body>
<div class="splash-header">
{mod_icon}
  <div class="splash-header-text">
    <h1>{title_html}</h1>
{author}
  </div>
</div>
{description}
{gameplay_image}
<div class="splash-section-title">Choose a faction</div>
<div class="splash-faction-grid">
{faction_cards}
</div>
</body>
</html>
"""
