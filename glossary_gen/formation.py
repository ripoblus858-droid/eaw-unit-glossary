"""
formation.py -- a squadron's formation-shape diagram, built from its
own Squadron_Offsets tags and rendered as a small self-contained SVG
(plus an HTML/CSS altitude-legend fragment).

Fully self-contained: reads Squadron_Offsets directly off a resolved
unit's own tag dict (parse_squadron_offsets) and turns it into
numeric/color markup with no HTML-escaping needed (no free-form text
is ever placed in this diagram), and with no dependency on the
merging/grouping logic in grouping.py.
"""


def parse_squadron_offsets(resolved):
    """Returns a list of (x, y, z) floats, one per Squadron_Offsets tag
    this squadron declares -- each triplet is one ship's position
    within the squadron's formation. A triplet that isn't exactly three
    comma-separated numbers is skipped rather than raising, since a
    formation diagram is a nice-to-have, not something worth crashing
    generation over if a source file has a malformed entry."""
    offsets = []
    for el in resolved.get("Squadron_Offsets", []):
        parts = [p.strip() for p in (el.text or "").split(",")]
        if len(parts) < 3:
            continue
        try:
            offsets.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return offsets


# Two-color gradient used to color a formation dot by its altitude (the
# third Squadron_Offsets number) -- reuses the page's existing blue
# accent (search highlight, links) and amber accent (the .note warning
# color) rather than introducing a new palette.
FORMATION_LOW_COLOR = (143, 184, 232)   # #8fb8e8
FORMATION_HIGH_COLOR = (201, 162, 74)   # #c9a24a

# Dot radius range for the formation diagram, keyed to altitude the
# same way color is -- see _formation_altitude_radius. A LOWER
# altitude gets the LARGER radius (MAX at t=0) and a higher altitude
# the smaller one (MIN at t=1): drawn with the lower/larger dots first
# and higher/smaller dots last (see render_formation_diagram's sort),
# a low-altitude ship sitting directly behind a high-altitude one at
# the same (x, y) still shows as a visible ring of color peeking out
# from behind the smaller dot on top, rather than the two positions
# looking identical to a single dot the way two equal-sized circles at
# the same point would.
#
# These are reference values for a size=FORMATION_DOT_RADIUS_REFERENCE_SIZE
# plot -- _formation_altitude_radius scales both proportionally to
# whatever `size` the diagram actually renders at, so a bigger plot
# also gets a bigger (and more visually distinguishable) radius gap
# between overlapping altitudes, instead of the ring staying a fixed,
# easy-to-miss half-pixel regardless of how large the diagram is drawn.
FORMATION_DOT_RADIUS_MIN = 3.0
FORMATION_DOT_RADIUS_MAX = 4.5
FORMATION_DOT_RADIUS_REFERENCE_SIZE = 110

# The formation plot is deliberately rendered LARGER than the unit/
# fighter icon images beside it (see render_group_row, which passes
# image_size * this multiplier as the diagram's `size`) -- a bigger
# canvas gives Squadron_Offsets more room to spread out, and (since
# _formation_altitude_radius scales dot radius with `size` too) also
# makes an altitude-only overlap's ring more visible than it would be
# at icon-sized scale.
FORMATION_PLOT_SIZE_MULTIPLIER = 2

# Fill opacity for a formation dot -- deliberately translucent (not
# fully opaque) so two dots plotted at (or very near) the same (x, y)
# position blend into a visibly darker/different-colored overlap
# instead of the top one fully hiding the one beneath it. Stroke stays
# fully opaque (see the circle markup) so each dot's own outline is
# still crisp even where dots overlap.
FORMATION_DOT_OPACITY = 0.7


def _formation_altitude_color(z, z_min, z_span):
    t = 0.5 if z_span <= 0 else max(0.0, min(1.0, (z - z_min) / z_span))
    r = round(FORMATION_LOW_COLOR[0] + (FORMATION_HIGH_COLOR[0] - FORMATION_LOW_COLOR[0]) * t)
    g = round(FORMATION_LOW_COLOR[1] + (FORMATION_HIGH_COLOR[1] - FORMATION_LOW_COLOR[1]) * t)
    b = round(FORMATION_LOW_COLOR[2] + (FORMATION_HIGH_COLOR[2] - FORMATION_LOW_COLOR[2]) * t)
    return f"rgb({r},{g},{b})"


def _formation_altitude_radius(z, z_min, z_span, size):
    """Linear dot radius by altitude -- t=0 (lowest altitude, z_min)
    maps to the (size-scaled) FORMATION_DOT_RADIUS_MAX, t=1 (highest
    altitude) maps to the (size-scaled) FORMATION_DOT_RADIUS_MIN -- the
    inverse direction of _formation_altitude_color's t, since here
    LOWER altitude is what should stand out as the larger dot. Same
    t=0.5 fallback as the color function for a single-altitude
    formation (z_span<=0), which lands exactly halfway between the two
    radii. Both FORMATION_DOT_RADIUS_MIN/MAX are defined at
    FORMATION_DOT_RADIUS_REFERENCE_SIZE and scaled by size/reference
    here, so the radius (and the gap between altitudes) grows with the
    diagram instead of staying a fixed pixel value regardless of how
    large `size` is."""
    scale = size / FORMATION_DOT_RADIUS_REFERENCE_SIZE
    radius_min = FORMATION_DOT_RADIUS_MIN * scale
    radius_max = FORMATION_DOT_RADIUS_MAX * scale
    t = 0.5 if z_span <= 0 else max(0.0, min(1.0, (z - z_min) / z_span))
    return radius_max - (radius_max - radius_min) * t


def render_formation_diagram(offsets, size=130):
    """Renders a squadron's formation-shape diagram from its
    Squadron_Offsets triplets. Returns (svg_html, legend_html) -- the
    legend is built as plain HTML/CSS (a gradient bar) rather than SVG,
    since that's simpler than hand-drawing gradient stops. Both are ""
    for fewer than 2 offsets -- nothing meaningful to draw.

    size is the SVG's viewBox/logical coordinate space AND its actual
    rendered pixel size (both the width/height attributes and an
    explicit inline width/height style set it to the same fixed value)
    -- every dot and the arrow are positioned in these units, all
    safely INSIDE [0, size] (no extra height for the arrow's text below
    the square) so the drawing itself is a clean square with no wasted
    margin. The "Formation" label is NOT part of this drawing -- it's
    plain HTML added by the caller (render_group_row), outside the
    plotted square but still inside the surrounding box, so it never
    overlaps a dot near an edge.
    The rendered size is deliberately FIXED rather than stretched to
    match some sibling column's height (an earlier version did that
    via CSS height:100% plus a JS resize listener measuring the
    rendered row) -- that made the diagram's size depend on how tall
    the squadron's stats table happened to be, and re-flowed on every
    window resize. A fixed pixel size is simpler and stable regardless
    of layout changes elsewhere in the row; `size` is both the
    coordinate system dots are plotted in AND the promised final pixel
    dimensions now. The altitude legend gets the same fixed height
    (via its own inline style, built alongside legend_html below) so
    it lines up with the SVG beside it without any JS involved.

    Coordinate reading, based on this mod's own offset patterns (the
    lead ship is always (0, 0, ...), every other ship's first number is
    0 or negative and gets more negative the further back a ship trails
    the leader, and the second number fans out in symmetric +/- pairs
    left and right of the leader): the FIRST number is the forward/back
    axis (0 = lead ship, more negative = further back), the SECOND is
    left/right spread, and the THIRD is a small vertical/altitude
    offset (this data only ever uses one of two discrete flight tiers,
    not a large spread). The diagram plots forward as "up" on screen
    (first coordinate -> vertical position, flipped so more-forward
    reads higher) with a small arrow marking that direction, plots
    left/right spread horizontally (second coordinate, unflipped), and
    colors AND sizes each dot by its altitude (third coordinate) via
    _formation_altitude_color / _formation_altitude_radius -- lower
    altitude gets both a cooler color and a larger radius, so a
    complete positional overlap between two different altitudes is
    still visible as a ring rather than a single dot (see
    FORMATION_DOT_RADIUS_MIN/MAX and the draw-order comment below)."""
    if len(offsets) < 2:
        return "", ""
    xs = [o[0] for o in offsets]  # forward/back
    ys = [o[1] for o in offsets]  # left/right
    zs = [o[2] for o in offsets]  # altitude
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    cx0 = (min(xs) + max(xs)) / 2
    cy0 = (min(ys) + max(ys)) / 2
    z_min, z_max = min(zs), max(zs)
    z_span = z_max - z_min

    pad = 14
    arrow_margin = 20  # reserved strip on the right for the forward arrow, kept clear of dots
    plot_h = size - 2 * pad
    plot_w = size - 2 * pad - arrow_margin
    center_x = pad + plot_w / 2
    center_y = pad + plot_h / 2

    dots = []
    # Draw lowest-altitude (largest) dots first and highest-altitude
    # (smallest) dots last -- SVG paints later elements on top, so a
    # ship sitting directly behind a higher-altitude one at the same
    # (x, y) still shows as a ring of its own color around the smaller
    # dot drawn over it, instead of the two positions being visually
    # indistinguishable from a single ship. Fill is translucent (see
    # FORMATION_DOT_OPACITY) for the same reason: a genuine full
    # overlap blends into a visibly different color rather than the
    # top dot fully occluding the one beneath it.
    for x, y, z in sorted(offsets, key=lambda o: o[2]):
        px = center_x + (y - cy0) / span * plot_w
        py = center_y - (x - cx0) / span * plot_h
        color = _formation_altitude_color(z, z_min, z_span)
        radius = _formation_altitude_radius(z, z_min, z_span, size)
        dots.append(
            f"<circle cx='{px:.1f}' cy='{py:.1f}' r='{radius:.2f}' fill='{color}' "
            f"fill-opacity='{FORMATION_DOT_OPACITY}' stroke='#0d0f14' stroke-width='0.6'/>"
        )

    # Arrow + its "FWD" label both sit fully inside [0, size] -- the
    # label sits just under the arrow's tail, not below the square.
    arrow_x = size - 11
    arrow_bottom = size - pad - 9
    arrow_top = pad + 3
    arrow_svg = (
        f"<line x1='{arrow_x}' y1='{arrow_bottom}' x2='{arrow_x}' y2='{arrow_top}' "
        f"stroke='#8a8f9c' stroke-width='1.5'/>"
        f"<polygon points='{arrow_x-4},{arrow_top} {arrow_x+4},{arrow_top} {arrow_x},{arrow_top-7}' fill='#8a8f9c'/>"
        f"<text x='{arrow_x}' y='{arrow_bottom+9}' text-anchor='middle' font-size='7' fill='#8a8f9c'>FWD</text>"
    )

    svg_html = (
        f"<svg viewBox='0 0 {size} {size}' width='{size}' height='{size}' class='formation-svg' "
        f"style='width:{size}px; height:{size}px;'>"
        f"<rect x='0' y='0' width='{size}' height='{size}' rx='8' fill='#0d0f14'/>"
        f"{''.join(dots)}{arrow_svg}</svg>"
    )

    legend_html = ""
    if z_span > 0:
        color_min = _formation_altitude_color(z_min, z_min, z_span)
        color_max = _formation_altitude_color(z_max, z_min, z_span)
        legend_html = (
            f"<div class='formation-legend' style='height:{size}px;'>"
            f"<div class='formation-legend-bar-row' style='height:{size}px;'>"
            f"<div class='formation-legend-labels' style='height:{size}px;'>"
            f"<span>{z_max:g}</span><span>{z_min:g}</span></div>"
            f"<div class='formation-legend-bar' style='height:{size}px; "
            f"background:linear-gradient(to bottom, {color_max}, {color_min});'></div>"
            f"</div>"
            f"<div class='formation-legend-title'>Altitude</div>"
            f"</div>"
        )
    return svg_html, legend_html
