#!/usr/bin/env python3
"""
generate_glossary.py

Scans a set of directories containing Empire at War / Forces of Corruption
(Alamo engine) style XML files, resolves file-level mod overrides and
Variant_Of_Existing_Type inheritance chains, and generates a single
self-contained HTML glossary page listing unit stats, hardpoints,
garrison complements, in-game screenshots, and (optionally) icon images.

This file is just the command-line entry point; the actual
implementation lives in the glossary_gen/ package next to this script,
split by concern (XML parsing, per-unit stat/model logic, merge/
grouping logic, the formation-diagram SVG, HTML rendering, page
CSS/JS/template, and top-level page assembly) -- see
glossary_gen/__init__.py for the full module breakdown, or run
`python3 generate_glossary.py --help` for usage.

USAGE
    python3 generate_glossary.py --dirs BASE_DIR [MOD_DIR ...] \
        --output glossary.html [--images-dir images] [--image-ext png]
"""

from glossary_gen.cli import main

if __name__ == "__main__":
    main()
