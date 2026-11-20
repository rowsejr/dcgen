"""Drawing palette. Three system hues (validated all-pairs for colour-blind
separation) - IT blue, electrical orange, mechanical aqua - plus neutrals for
everything else. Identity never relies on colour alone: everything is labelled."""
from __future__ import annotations

import matplotlib
from matplotlib import font_manager

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
HAIR, BASELINE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
REDLINE = "#d03b3b"
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
            "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def tint(h, t):
    """Mix towards white by t (0..1)."""
    r = _rgb(h)
    return _hex(tuple(c + (1 - c) * t for c in r))


def shade(h, t):
    """Mix towards black by t (0..1)."""
    return _hex(tuple(c * (1 - t) for c in _rgb(h)))


# base colour per element kind (3D faces are shaded from this; plans use lighter tints)
BASE = {
    "data_hall": tint(BLUE, 0.42),
    "support": "#d6d3cb", "corridor": "#dedbd4",
    "mech_gallery": tint(AQUA, 0.55),
    "elec_room": tint(ORANGE, 0.5), "sts_room": tint(ORANGE, 0.35), "mv_room": tint(ORANGE, 0.25),
    "transformer": shade(ORANGE, 0.28), "genset": ORANGE,
    "chiller": AQUA, "dry_cooler": AQUA,
    "substation": tint(ORANGE, 0.2),
    "sprinkler_tank": "#bab7ae", "pump_house": "#a3a097",
    "fuel_bund": "#e2e0da", "fuel_tank": "#7f7c75",
    "guardhouse": "#6f6c66", "gate": INK,
    "road": "#d2d0ca", "parking": "#e4e2dc", "landscape": "#e1eedb",
}
PLAN_FILL = {k: (tint(v, 0.45) if k in ("data_hall", "mech_gallery", "elec_room", "sts_room", "mv_room")
                 else v) for k, v in BASE.items()}

GROUPS = [  # legend groups
    ("Data halls (IT)", BASE["data_hall"]),
    ("Electrical: rooms, transformers, gensets, substation", ORANGE),
    ("Mechanical: heat rejection, mech gallery", AQUA),
    ("Support, fire, fuel, security", "#a3a097"),
    ("Roads and parking", BASE["road"]),
    ("Landscape buffer", BASE["landscape"]),
]


def setup_fonts():
    # static TrueType faces embed cleanly as Type 42 (OTF/CFF faces such as Inter do not)
    ttf = {f.name for f in font_manager.fontManager.ttflist if f.fname.lower().endswith(".ttf")}
    fam = next((f for f in ("Fira Sans", "Carlito", "DejaVu Sans") if f in ttf), "DejaVu Sans")
    matplotlib.rcParams.update({
        "font.family": fam, "font.size": 7, "pdf.fonttype": 42, "svg.fonttype": "none",
        "axes.edgecolor": BASELINE, "text.color": INK, "axes.labelcolor": INK2,
        "xtick.color": MUTED, "ytick.color": MUTED,
    })
    return fam
