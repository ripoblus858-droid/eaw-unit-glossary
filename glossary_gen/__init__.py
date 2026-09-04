"""
glossary_gen -- generates a self-contained HTML unit glossary from a
set of Empire at War / Forces of Corruption (Alamo engine) XML files.

See generate_glossary.py (the CLI entry point one directory up) for
usage. This package is organized by concern:

    xml_io          reading/parsing XML into a registry + resolved units
    model           pure per-unit derived stats (no HTML, no merging)
    grouping        which candidates fold/merge into which cards
    formation       a squadron's formation-shape SVG diagram
    render          building one card's HTML (render_group_row)
    styles          the page's CSS (PAGE_CSS)
    page_script     the page's JavaScript (PAGE_SCRIPT)
    page_template   the page's HTML skeleton (PAGE_TEMPLATE)
    html_output     top-level page assembly (generate_html)
    cli             argument parsing and the main() entry point
"""
