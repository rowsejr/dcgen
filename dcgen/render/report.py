"""A3 landscape PDF report: vector sheets with a title block on the right.

Sheets: option study, 3D overview, site plan, 3D close-up, electrical strategy,
heat rejection + noise, schedules + checks. All vector (matplotlib PdfPages).
"""
from __future__ import annotations

import datetime as dt
import logging
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.transforms import Affine2D

from ..catalogue import load_catalogue
from ..engines import noise
from ..engines.cooling import unit_capacity_kw
from ..schema import Scene
from .axo import draw_axo
from .plan import LabelPlacer, draw_labels, draw_north, draw_plan, draw_scalebar, plan_rotation
from .style import (AQUA, BASELINE, BLUE, CRIT, GOOD, GROUPS, HAIR, INK, INK2, MUTED, ORANGE, REDLINE,
                    SEQ_BLUE, WARN, setup_fonts, tint)

logging.getLogger("fontTools").setLevel(logging.WARNING)   # silence PDF font subsetting chatter
logging.getLogger("matplotlib").setLevel(logging.WARNING)

A3 = (16.535, 11.693)
MX, MY = 10 / 420, 10 / 297            # 10 mm margins
STRIP_W = 82 / 420                      # title strip width
GAP = 4 / 420
DRAW = (MX, MY, 1 - 2 * MX - STRIP_W - GAP, 1 - 2 * MY)   # drawing area (x, y, w, h) in figure fraction
SEV = {"pass": (GOOD, "PASS"), "warn": (WARN, "WARN"), "fail": (CRIT, "FAIL"), "info": (MUTED, "INFO")}


# ----------------------------------------------------------------------------- frame + title block
def _sheet(title: str, scene: Scene, sheet_no: str, scale: str = "NTS", legend: bool = True, kpis: bool = True):
    fig = plt.figure(figsize=A3)
    fig.patch.set_facecolor("white")
    # drawing frame
    fr = fig.add_axes([0, 0, 1, 1], zorder=-10)
    fr.axis("off")
    fr.add_patch(Rectangle((MX - 2 / 420, MY - 2 / 297), 1 - 2 * MX + 4 / 420, 1 - 2 * MY + 4 / 297,
                           fill=False, ec=INK, lw=0.8, transform=fig.transFigure))
    x0 = 1 - MX - STRIP_W
    fr.add_line(Line2D([x0 - GAP / 2] * 2, [MY - 2 / 297, 1 - MY + 2 / 297], color=INK, lw=0.6,
                       transform=fig.transFigure))
    tb = fig.add_axes([x0, MY, STRIP_W, 1 - 2 * MY])
    tb.axis("off")
    tb.set_xlim(0, 1)
    tb.set_ylim(0, 1)
    b, d = scene.brief, scene.designs
    e, c, m = d["electrical"], d["cooling"], scene.metrics

    y = 0.985
    tb.add_patch(FancyBboxPatch((0.02, y - 0.055), 0.40, 0.05, boxstyle="round,pad=0.0,rounding_size=0.01",
                                fc="#f1f0ec", ec=BASELINE, lw=0.5))
    tb.text(0.22, y - 0.03, "[Practice logo]", ha="center", va="center", fontsize=7, color=MUTED)
    tb.text(0.98, y - 0.012, "dcgen", ha="right", va="top", fontsize=13, fontweight="bold", color=INK)
    tb.text(0.98, y - 0.040, "test-fit generator v0.1", ha="right", va="top", fontsize=6.5, color=INK2)
    y -= 0.075
    tb.text(0.02, y, b.project, fontsize=10, fontweight="bold", va="top", wrap=True)
    y -= 0.028
    tb.text(0.02, y, f"{b.client}  |  {b.stage}", fontsize=7, color=INK2, va="top")
    y -= 0.022
    tb.add_line(Line2D([0.02, 0.98], [y, y], color=HAIR, lw=0.8))
    y -= 0.012
    tb.text(0.02, y, title, fontsize=11, fontweight="bold", va="top", color=INK)
    y -= 0.03
    tb.text(0.02, y, f"Option {scene.option}  -  {_arrangement(scene)}", fontsize=6.6, color=INK2, va="top")
    y -= 0.03

    if kpis:
        noise_rows = m.get("noise", [])
        worst = min((r["margin_db"] for r in noise_rows), default=None)
        rows = [
            ("IT load", f"{b.it_load_mw:g} MW in {m['data_halls']} halls"),
            ("Racks", f"{m['racks']:,} at {b.rack_kw:g} kW"),
            ("Topology", f"{e['topology']}, {e['blocks']} x {e['block_ups_kw'] / 1000:.1f} MW blocks"),
            ("Electrical rooms", f"{e['elec_rooms']} (+{e['sts_rooms']} STS, 1 MV) on {d['core']['elec_levels']} levels"),
            ("Gensets", f"{e['gensets']} x {e['genset'].replace(' genset', '')}"),
            ("Heat rejection", f"{c['total']} x {c['unit_capacity_at_design_kw']:,} kW at {b.ambient_design_c:g}°C"),
            ("Site / coverage", f"{m['site_area_ha']} ha / {m['coverage_pct']}%"),
            ("GIA / footprint", f"{m['gia_m2']:,} / {m['footprint_m2']:,} m²"),
            ("Spare land", f"~{m.get('spare_land_ha', 0):.1f} ha"),
            ("Worst noise margin", "n/a" if worst is None else f"{worst:+.1f} dB"),
        ]
        for k, v in rows:
            tb.text(0.02, y, k, fontsize=6.4, color=INK2, va="top")
            tb.text(0.98, y, v, fontsize=6.4, color=INK, va="top", ha="right")
            y -= 0.0215
        y -= 0.008
    if legend:
        tb.add_line(Line2D([0.02, 0.98], [y, y], color=HAIR, lw=0.8))
        y -= 0.016
        tb.text(0.02, y, "LEGEND", fontsize=6, color=MUTED, va="top", fontweight="bold")
        y -= 0.022
        for name, col in GROUPS:
            tb.add_patch(Rectangle((0.02, y - 0.012), 0.06, 0.013, fc=col, ec="none"))
            tb.text(0.10, y - 0.006, name, fontsize=6, va="center", color=INK)
            y -= 0.02
        tb.add_line(Line2D([0.02, 0.08], [y - 0.006] * 2, color=REDLINE, lw=1.1, ls=(0, (5, 2))))
        tb.text(0.10, y - 0.006, "Planning redline", fontsize=6, va="center")
        y -= 0.02
        tb.add_line(Line2D([0.02, 0.08], [y - 0.006] * 2, color=MUTED, lw=0.5, ls=(0, (2, 2))))
        tb.text(0.10, y - 0.006, f"Boundary setback ({scene.site.boundary_setback_m:g} m)", fontsize=6, va="center")
        y -= 0.03

    # notes
    notes = [
        "Indicative RIBA Stage 2 test fit generated from the brief and catalogue v0.1.",
        "Plant sizes, clearances and noise data are placeholders until checked.",
        "Noise is a screening estimate (hemispherical spreading, simple screening).",
        "Not for construction. Not a planning drawing.",
    ]
    yy = 0.235
    tb.add_line(Line2D([0.02, 0.98], [yy + 0.012, yy + 0.012], color=HAIR, lw=0.8))
    tb.text(0.02, yy, "NOTES", fontsize=6, color=MUTED, va="top", fontweight="bold")
    yy -= 0.02
    for i, n in enumerate(notes, 1):
        tb.text(0.02, yy, f"{i}.", fontsize=5.8, color=INK2, va="top")
        tb.text(0.07, yy, n, fontsize=5.8, color=INK2, va="top", wrap=True)
        yy -= 0.02

    # status + sheet block
    tb.add_patch(Rectangle((0.02, 0.083), 0.96, 0.042, fc=tint(WARN, 0.75), ec="none"))
    tb.text(0.5, 0.104, "INDICATIVE  -  NOT FOR CONSTRUCTION", ha="center", va="center", fontsize=7.5,
            fontweight="bold", color=INK)
    cells = [("Date", dt.date.today().isoformat()), ("Rev", "P01"), ("Scale", scale), ("Sheet", sheet_no)]
    for i, (k, v) in enumerate(cells):
        cx = 0.02 + i * 0.24
        tb.add_patch(Rectangle((cx, 0.0), 0.24, 0.07, fill=False, ec=BASELINE, lw=0.5))
        tb.text(cx + 0.015, 0.06, k.upper(), fontsize=5.2, color=MUTED, va="top")
        tb.text(cx + 0.015, 0.028, v, fontsize=7.2, color=INK, va="center", fontweight="bold")
    return fig


def _arrangement(scene: Scene) -> str:
    return scene.variant.get("label", "")


def _title(fig, text, sub=None):
    x, y = DRAW[0], DRAW[1] + DRAW[3]
    fig.text(x, y - 0.002, text, fontsize=11, fontweight="bold", va="top", color=INK)
    if sub:
        fig.text(x, y - 0.024, sub, fontsize=6.8, color=INK2, va="top")


def _wrap(text, width=110):
    import textwrap
    return "\n".join(textwrap.wrap(text, width))


def _draw_ax(fig, rect=None):
    x, y, w, h = rect or DRAW
    return fig.add_axes([x, y, w, h])


# ----------------------------------------------------------------------------- tables
def table(ax, x, y, cols, rows, widths, fs=6.4, row_h=0.034, header=True, aligns=None, bold_last=False):
    """Simple hairline table in axes coordinates. Returns the y below the table."""
    aligns = aligns or (["left"] + ["right"] * (len(cols) - 1))
    xs = [x]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    def put(vals, yy, **kw):
        for xi, w, v, al in zip(xs, widths, vals, aligns):
            tx = xi + (w - 0.005 if al == "right" else 0.004)
            if isinstance(v, tuple) and v and v[0] == "status":
                col, lab = SEV[v[1]]
                ax.plot([xi + 0.008], [yy - row_h * 0.45], marker="o", ms=fs * 0.75, color=col,
                        transform=ax.transAxes, clip_on=False)
                ax.text(xi + 0.018, yy - row_h * 0.45, lab, fontsize=fs - 0.6, va="center", ha="left",
                        color=INK, fontweight="bold", transform=ax.transAxes)
                continue
            ax.text(tx, yy - row_h * 0.45, str(v), fontsize=fs, va="center", ha=al, transform=ax.transAxes, **kw)
    if header:
        put(cols, y, color=INK2, fontweight="bold")
        y -= row_h
        ax.add_line(Line2D([x, x + sum(widths)], [y, y], color=INK2, lw=0.6, transform=ax.transAxes))
    for i, r in enumerate(rows):
        put(r, y, color=INK, fontweight="bold" if bold_last and i == len(rows) - 1 else "normal")
        y -= row_h
        ax.add_line(Line2D([x, x + sum(widths)], [y, y], color=HAIR, lw=0.5, transform=ax.transAxes))
    return y


def _heading(ax, x, y, text, sub=None):
    ax.text(x, y, text, fontsize=10, fontweight="bold", va="top", transform=ax.transAxes)
    if sub:
        ax.text(x, y - 0.03, sub, fontsize=6.6, color=INK2, va="top", transform=ax.transAxes)


# ----------------------------------------------------------------------------- sheets
def sheet_options(scene: Scene, options: list[Scene]):
    fig = _sheet("Option study", scene, "DC-001", legend=True)
    n_var = options[0].metrics.get("variants_tested", 16)
    _title(fig, f"{len(options)} best of {n_var} arrangements",
           "Arrangement = orientation x support end x electrical side x heat rejection location; several positions "
           "each, ranked by checks, noise margin, cable and road lengths.")
    n = len(options)
    w = 1.0 / max(n, 1)
    subs = []
    for i, o in enumerate(options):
        sub = fig.add_axes([DRAW[0] + DRAW[2] * (i * w + 0.006), DRAW[1] + DRAW[3] * 0.30,
                            DRAW[2] * (w - 0.012), DRAW[3] * 0.60])
        draw_plan(sub, o, labels=False, show_racks=False, margin=6)
        sub.set_anchor("N")
        subs.append(sub)
        sel = o.option == scene.option
        sub.set_title(f"Option {o.option}" + ("  (selected)" if sel else ""), fontsize=9,
                      fontweight="bold", color=INK, loc="left")
        fails, warns = o.metrics["checks_failed"], o.metrics["checks_warn"]
        sev = "fail" if fails else ("warn" if warns else "pass")
        col, lab = SEV[sev]
        sub.plot([0.012], [-0.035], marker="o", ms=5, color=col, transform=sub.transAxes, clip_on=False)
        sub.text(0.03, -0.035, f"{lab}   {fails} fail, {warns} warn   score {o.score:.0f}", color=INK,
                 fontsize=6.6, transform=sub.transAxes, va="center", fontweight="bold" if sel else "normal")
    fig.canvas.draw()
    bottom = min(s_.get_position().y0 for s_ in subs) - 0.05
    ax = fig.add_axes([DRAW[0], DRAW[1], DRAW[2], max(0.15, bottom - DRAW[1])])
    ax.axis("off")
    rows = []
    for o in options:
        m = o.metrics
        nz = " / ".join(f"{r['margin_db']:+.1f}" for r in m["noise"]) or "-"
        sev = "fail" if m["checks_failed"] else ("warn" if m["checks_warn"] else "pass")
        rows.append([o.option, _arrangement(o), ("status", sev), nz, f"{m['substation_to_elec_m']:.0f}",
                     f"{m['fuel_to_genyard_m']:.0f}", f"{m['road_area_m2']:,}", f"{m['spare_land_ha']:.1f}",
                     f"{o.score:.0f}"])
    table(ax, 0.0, 0.98, ["Opt", "Arrangement", "Checks", "Noise margin dB (NSRs)", "Sub-elec m",
                          "Fuel-gen m", "Road m\u00b2", "Spare ha", "Score"], rows,
          [0.04, 0.37, 0.08, 0.14, 0.08, 0.08, 0.08, 0.07, 0.06], fs=6.8, row_h=min(0.11, 0.85 / (len(rows) + 1)),
          aligns=["left", "left", "left", "right", "right", "right", "right", "right", "right"])
    ax.text(0.0, 0.0, "Score: lower is better. Weights live in dcgen/layout.py:score() for the team to tune.",
            fontsize=6, color=MUTED, transform=ax.transAxes)
    return fig


def sheet_axo(scene: Scene, focus="site", az=-50, el=32, title="3D overview", no="DC-002"):
    fig = _sheet(title, scene, no)
    ax = _draw_ax(fig, (DRAW[0], DRAW[1] + 0.02, DRAW[2], DRAW[3] - 0.04))
    draw_axo(ax, scene, az=az, el=el, focus=focus, fontsize=6.5)
    _title(fig, title, f"View from the {_view_dir(scene, az)}, axonometric, not to scale")
    return fig


def _view_dir(scene, az):
    t = math.radians(scene.frame.rotation_deg + az)
    brg = (math.degrees(math.atan2(math.cos(t), math.sin(t))) + scene.site.true_north_deg) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][
        int((brg + 22.5) // 45) % 8]


def sheet_plan(scene: Scene):
    probe = plt.figure(figsize=A3)
    pax = probe.add_axes(DRAW)
    draw_plan(pax, scene, margin=22, labels=False)
    scale = next((sc for sc in (500, 750, 1000, 1250, 1500, 2000, 2500, 5000) if _fix_scale(probe, pax, sc)), None)
    plt.close(probe)
    fig = _sheet("Site plan", scene, "DC-003", scale=f"1:{scale} @ A3" if scale else "NTS")
    ax = _draw_ax(fig)
    draw_plan(ax, scene, margin=22, labels=False)
    if scale:
        _fix_scale(fig, ax, scale)
    draw_labels(ax, scene)
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    draw_north(ax, x1 - 14, y1 - 22, 10, 90)
    draw_scalebar(ax, x0 + 6, y0 + 6, 100)
    _title(fig, "Site plan", "Roof plant and upper levels dashed. Dotted hatch: spare land. "
                             "Electrical room ids show level 1 / level 2.")
    return fig


def _fix_scale(fig, ax, scale):
    """Set the axes limits so that 1 m on paper = 1000/scale mm (true scale on A3)."""
    bb = ax.get_position()
    w_mm, h_mm = bb.width * 420, bb.height * 297
    cx, cy = np.mean(ax.get_xlim()), np.mean(ax.get_ylim())
    half_w = w_mm * scale / 1000 / 2
    half_h = h_mm * scale / 1000 / 2
    need_w = (ax.get_xlim()[1] - ax.get_xlim()[0]) / 2
    need_h = (ax.get_ylim()[1] - ax.get_ylim()[0]) / 2
    if half_w >= need_w and half_h >= need_h:
        ax.set_xlim(cx - half_w, cx + half_w)
        ax.set_ylim(cy - half_h, cy + half_h)
        ax.set_aspect("equal", adjustable="box")
        return True
    return False


def draw_topology(ax, scene: Scene):
    e = scene.designs["electrical"]
    x, y, kind = e["x"], e["y"], e["topology_kind"]
    pairs = [tuple(p) for p in e["pairs"]]
    L = "ABCDEFGHIJKL"
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    bw, bh = min(0.14, 0.8 / x), 0.11
    bx = [0.1 + (0.8 - bw) * (i / max(1, x - 1)) for i in range(x)]
    by = 0.70
    blk_kw = e["block_ups_kw"]
    for i in range(x):
        is_c = kind == "catcher" and i == x - 1
        col = tint(ORANGE, 0.35 if not is_c else 0.7)
        ax.add_patch(FancyBboxPatch((bx[i], by), bw, bh, boxstyle="round,pad=0.004,rounding_size=0.01",
                                    fc=col, ec=ORANGE, lw=0.8))
        name = ("Catcher" if is_c else f"Block {L[i]}")
        load = "0%" if is_c else (f"{e['normal_loading_pct']}%" if kind != "2N" else "50%")
        ax.text(bx[i] + bw / 2, by + bh * 0.66, name, ha="center", va="center", fontsize=6.6, fontweight="bold")
        ax.text(bx[i] + bw / 2, by + bh * 0.30, f"{blk_kw / 1000:.1f} MW | {load}", ha="center", va="center",
                fontsize=5.6, color=INK2)
    npair = len(pairs)
    lw_, lh = min(0.11, 0.84 / max(npair, 1) - 0.012), 0.09
    lx = [0.08 + (0.84 - lw_) * (j / max(1, npair - 1)) for j in range(npair)]
    ly = 0.24
    for j, (p, q) in enumerate(pairs):
        ax.add_patch(FancyBboxPatch((lx[j], ly), lw_, lh, boxstyle="round,pad=0.004,rounding_size=0.008",
                                    fc=tint(BLUE, 0.6), ec=BLUE, lw=0.7))
        lbl = f"{L[p]}+{L[q]}" if kind != "catcher" else f"Load {L[p]}"
        share = (f"1/{npair}" if kind == "DR" else ("1/1" if kind == "2N" else f"1/{y}"))
        ax.text(lx[j] + lw_ / 2, ly + lh * 0.64, lbl, ha="center", va="center", fontsize=6, fontweight="bold")
        ax.text(lx[j] + lw_ / 2, ly + lh * 0.28, f"{share} load", ha="center", va="center", fontsize=5.2, color=INK2)
        for k, b in enumerate((p, q)):
            sx = bx[b] + bw / 2
            ex = lx[j] + lw_ * (0.3 if k == 0 else 0.7)
            dashed = (kind == "catcher" and b == x - 1) or (kind != "catcher" and k == 1)
            ax.add_line(Line2D([sx, ex], [by, ly + lh], color=INK2, lw=0.6,
                               ls=(0, (3, 2)) if dashed else "-"))
    if kind == "DR":
        cap = (f"{e['topology']} distributed redundant: the group's IT load is split into {npair} rack groups, each "
               f"dual-corded to a different pair of blocks. Normal running {e['normal_loading_pct']}% per block; "
               f"lose any one block and its {x - 1} partners rise to 100%.")
    elif kind == "catcher":
        cap = (f"{e['topology']}: each primary block carries its own load (100% rated). The catcher picks up a "
               f"failed primary through static transfer switches (dashed = STS path).")
    else:
        cap = "2N: A and B systems each rated for the full load; 50% normal loading."
    ax.text(0.0, 0.12, _wrap(cap, 120), fontsize=6.4, color=INK2, va="top")
    ax.text(0.0, 0.98, f"One redundancy group (of {e['groups']})", fontsize=7.5, fontweight="bold", va="top")
    ax.add_line(Line2D([0.0, 0.05], [0.04, 0.04], color=INK2, lw=0.6))
    ax.text(0.06, 0.04, "A feed", fontsize=5.6, va="center", color=INK2)
    ax.add_line(Line2D([0.16, 0.21], [0.04, 0.04], color=INK2, lw=0.6, ls=(0, (3, 2))))
    ax.text(0.22, 0.04, "B feed / STS", fontsize=5.6, va="center", color=INK2)


def sheet_electrical(scene: Scene, topo_rows: list[dict]):
    fig = _sheet("Electrical strategy", scene, "DC-005", legend=False)
    ax = _draw_ax(fig)
    ax.axis("off")
    e = scene.designs["electrical"]
    _title(fig, f"Electrical topology {e['topology']}",
           f"{e['blocks']} blocks x {e['block_ups_kw']:,} kW UPS = {e['installed_ups_mw']} MW installed for "
           f"{scene.brief.it_load_mw:g} MW IT. Site demand ~{e['site_demand_mva']:.1f} MVA -> {e['substation']} intake.")
    tax = fig.add_axes([DRAW[0], DRAW[1] + DRAW[3] * 0.44, DRAW[2] * 0.56, DRAW[3] * 0.46])
    draw_topology(tax, scene)

    # comparison chart: installed UPS MW by topology (single series, selected highlighted)
    cax = fig.add_axes([DRAW[0] + DRAW[2] * 0.64, DRAW[1] + DRAW[3] * 0.50, DRAW[2] * 0.34, DRAW[3] * 0.36])
    names = [r["topology"] for r in topo_rows]
    vals = [r["installed_mw"] for r in topo_rows]
    sel = [n == e["topology"] for n in names]
    ys = np.arange(len(names))[::-1]
    cax.barh(ys, vals, height=0.5, color=[BLUE if s else tint(BLUE, 0.55) for s in sel], edgecolor="white",
             linewidth=1.0)
    for yy, r, s in zip(ys, topo_rows, sel):
        cax.text(r["installed_mw"] + max(vals) * 0.015, yy,
                 f"{r['installed_mw']:g} MW  |  {r['blocks']} blocks, {r['gensets']} gensets", va="center",
                 fontsize=6, color=INK if s else INK2, fontweight="bold" if s else "normal")
    cax.set_yticks(ys, names, fontsize=6.6)
    cax.set_xlim(0, max(vals) * 1.75)
    cax.axvline(scene.brief.it_load_mw, color=INK2, lw=0.6, ls=(0, (2, 2)))
    cax.set_ylim(-0.6, len(names) - 0.1)
    cax.text(scene.brief.it_load_mw, len(names) - 0.45, f" IT load {scene.brief.it_load_mw:g} MW", fontsize=5.6,
             color=INK2, va="center")
    for sp in ("top", "right"):
        cax.spines[sp].set_visible(False)
    cax.spines["left"].set_color(BASELINE)
    cax.spines["bottom"].set_color(BASELINE)
    cax.tick_params(axis="x", labelsize=6, colors=MUTED)
    cax.tick_params(axis="y", length=0)
    cax.set_xlabel("Installed UPS capacity (MW)", fontsize=6.4, color=INK2)
    cax.set_title("Same brief, other topologies", fontsize=7.5, fontweight="bold", loc="left", color=INK)

    # plant schedule
    rows = [
        ["UPS blocks", f"{e['blocks']}", f"{e['block_ups_kw']:,} kW each, {e['normal_loading_pct']}% normal"],
        ["Electrical rooms", f"{e['elec_rooms']}", f"{scene.designs['core']['elec_levels']} levels, "
                                                   f"{scene.brief.blocks_per_elec_room} block(s) per room"],
        ["STS rooms", f"{e['sts_rooms']}", "catcher topologies only"],
        ["MV switchroom", "1", "customer intake"],
        ["Transformers", f"{e['transformers']}", e["transformer"]],
        ["Gensets", f"{e['gensets']}", f"{e['genset']}, {scene.designs['core']['gen_rows']} row(s)"],
        ["Block facility load", "", f"{e['facility_kw_per_block']:,} kW (UPS x design PUE {scene.brief.design_pue:g})"],
        ["Fuel", f"{e['fuel_tanks']} tanks", f"{e['fuel_m3']:,} m³ for {scene.brief.fuel_autonomy_h:g} h at design load"],
        ["Utility intake", e["substation"], f"~{e['site_demand_mva']} MVA site demand"],
    ]
    _heading(ax, 0.0, 0.40, "Plant schedule")
    table(ax, 0.0, 0.355, ["Item", "Qty", "Basis"], rows, [0.16, 0.08, 0.32], fs=6.4, row_h=0.034,
          aligns=["left", "right", "left"])
    rows2 = [[r["topology"], r["blocks"], f"{r['block_kw']:,}", f"{r['installed_mw']:g}", f"{r['normal_loading_pct']}%",
              r["gensets"], r["elec_rooms"], r["sts_rooms"]] for r in topo_rows]
    _heading(ax, 0.62, 0.40, "Topology comparison")
    table(ax, 0.62, 0.355, ["Topology", "Blocks", "Block kW", "MW", "Normal", "Gensets", "Rooms", "STS"], rows2,
          [0.085, 0.042, 0.052, 0.035, 0.045, 0.048, 0.04, 0.03], fs=6.2, row_h=0.034)
    return fig


def sheet_noise(scene: Scene):
    fig = _sheet("Heat rejection and noise", scene, "DC-006", legend=False)
    ax = fig.add_axes([DRAW[0], DRAW[1], DRAW[2] * 0.60, DRAW[3] * 0.92])
    rot = plan_rotation(scene)
    R = Affine2D().rotate_deg(rot)
    pts = [scene.frame.to_local(r.x, r.y) for r in scene.site.receptors]
    allp = np.vstack([R.transform(np.asarray(scene.redline_local))] + ([R.transform(np.asarray(pts))] if pts else []))
    ext = (*allp.min(0), *allp.max(0))
    draw_plan(ax, scene, muted=True, labels=False, margin=40, extent=ext)
    gx, gy, lp = noise.noise_grid(scene, pad=60, step=5)
    tr = Affine2D().rotate_deg(rot) + ax.transData
    levels = [30, 35, 40, 45, 50, 55, 60, 65]
    cols = [SEQ_BLUE[i] for i in (0, 2, 4, 6, 8, 10, 12)]
    cs = ax.contourf(gx, gy, lp, levels=levels, colors=cols, alpha=0.55, transform=tr, zorder=3, extend="max")
    lims = sorted({r.limit_dba for r in scene.site.receptors})
    if lims:
        cl = ax.contour(gx, gy, lp, levels=lims, colors=[INK], linewidths=0.8, transform=tr, zorder=4)
        ax.clabel(cl, fmt=lambda v: f"{v:.0f} dB(A)", fontsize=5.6, inline=True)
    lpl = LabelPlacer(ax, fontsize=6)
    for r in noise.receptor_levels(scene):
        px, py = R.transform((r["x_local"], r["y_local"]))
        sev = "pass" if r["margin_db"] >= 0 else "fail"
        ax.plot(px, py, marker="o", ms=5, mfc="white", mec=INK, mew=0.9, zorder=50)
        lpl.add(f"{r['name']}\n{r['lp_dba']:.1f} dB(A), limit {r['limit_dba']:.0f}  {SEV[sev][1]}", (px, py),
                offsets=[(0, 16), (0, -16), (40, 0), (-40, 0)], leader=True)
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    draw_north(ax, x1 - 12, y1 - 20, 9, 90)
    draw_scalebar(ax, x0 + 6, y0 + 6, 100)
    _title(fig, "Screening noise map (operational heat rejection)",
           "Lp at 1.5 m, dB(A). Gensets excluded (emergency/test running assessed separately). "
           "Black contours = receptor limits.")
    # colour key under the (aspect-shrunk) map
    fig.canvas.draw()
    pos = ax.get_position()
    kx = fig.add_axes([pos.x0, max(DRAW[1], pos.y0 - 0.03), pos.width * 0.45, 0.011])
    for i, c in enumerate(cols):
        kx.add_patch(Rectangle((i, 0), 1, 1, fc=c, alpha=0.55, ec="white", lw=0.8))
        kx.text(i, -0.4, str(levels[i]), fontsize=5.4, ha="center", va="top", color=INK2)
    kx.text(len(cols), -0.4, f"{levels[-1]}+ dB(A)", fontsize=5.4, ha="left", va="top", color=INK2)
    kx.set_xlim(0, len(cols))
    kx.set_ylim(0, 1)
    kx.axis("off")

    # right column: selection + derating chart + receptor table
    rx = fig.add_axes([DRAW[0] + DRAW[2] * 0.63, DRAW[1], DRAW[2] * 0.37, DRAW[3]])
    rx.axis("off")
    c = scene.designs["cooling"]
    b = scene.brief
    _heading(rx, 0.0, 1.0, "Heat rejection selection")
    rows = [["Heat load", f"{c['heat_load_mw']} MW", f"IT +{b.hr_overhead_frac:.0%}"],
            ["Unit", c["type"].replace("_", " "), c["unit"].split(" ~")[0]],
            ["Capacity at design", f"{c['unit_capacity_at_design_kw']:,} kW", f"{b.ambient_design_c:g}°C dry bulb"],
            ["Duty + standby", f"{c['duty']} + {c['standby']}", b.hr_redundancy],
            ["Margin (duty only)", f"{c['capacity_margin_pct']:+.1f}%", ""],
            ["Location", scene.variant.get("hr_location", ""), f"{scene.designs['core']['hr_on_roof']} roof / "
                                                              f"{scene.designs['core']['hr_on_ground']} ground"]]
    table(rx, 0.0, 0.955, ["", "Value", "Basis"], rows, [0.36, 0.30, 0.34], fs=6.4, row_h=0.032,
          aligns=["left", "right", "right"])
    # derating chart
    ch = fig.add_axes([DRAW[0] + DRAW[2] * 0.665, DRAW[1] + DRAW[3] * 0.41, DRAW[2] * 0.31, DRAW[3] * 0.23])
    unit = next(u for u in load_catalogue()["heat_rejection"].values() if u["label"] == c["unit"])
    ts = np.arange(15, 46, 0.5)
    caps = [unit_capacity_kw(unit, b.model_copy(update={"ambient_design_c": float(t)})) / 1000 for t in ts]
    ch.plot(ts, caps, color=AQUA, lw=1.6)
    dc = unit_capacity_kw(unit, b) / 1000
    ch.plot([b.ambient_design_c], [dc], marker="o", ms=5, color=AQUA, mec="white", mew=1.0, zorder=5)
    ch.annotate(f"design {b.ambient_design_c:g}°C: {dc * 1000:,.0f} kW", (b.ambient_design_c, dc),
                xytext=(-8, -16), textcoords="offset points", fontsize=5.8, ha="right", color=INK)
    ch.set_ylim(0, max(caps) * 1.15)
    ch.set_xlabel("Ambient dry bulb (°C)", fontsize=6.2, color=INK2)
    ch.set_ylabel("Capacity per unit (MW)", fontsize=6.2, color=INK2)
    ch.tick_params(labelsize=5.8, colors=MUTED)
    ch.grid(axis="y", color=HAIR, lw=0.5)
    for sp in ("top", "right"):
        ch.spines[sp].set_visible(False)
    ch.set_title("Unit capacity vs ambient (indicative curve)", fontsize=7, fontweight="bold", loc="left")
    rows = []
    for r in noise.receptor_levels(scene):
        rows.append([r["name"], f"{r['lp_dba']:.1f}", f"{r['limit_dba']:.0f}", f"{r['margin_db']:+.1f}",
                     ("status", "pass" if r["margin_db"] >= 0 else "fail")])
    _heading(rx, 0.0, 0.31, "Noise at receptors (screening)")
    table(rx, 0.0, 0.265, ["Receptor", "Lp dB(A)", "Limit", "Margin", "Check"], rows,
          [0.40, 0.15, 0.12, 0.14, 0.17], fs=6.2, row_h=0.034)
    rx.text(0.0, 0.08, "Method: Lp = Lw - 20log(r) - 8 - screening (roof parapet, yard barrier, building).\n"
                       "Replace with ISO 9613-2 / CadnaA before acoustic sign-off (BS 4142 context).",
            fontsize=5.8, color=MUTED, va="top")
    return fig


def sheet_schedules(scene: Scene):
    fig = _sheet("Schedules and checks", scene, "DC-007", legend=False)
    ax = _draw_ax(fig)
    ax.axis("off")
    els = scene.elements
    def area(kind):
        return sum(e.w * e.d for e in els if e.kind == kind)
    s = max(1, scene.brief.storeys)
    h = scene.designs["halls"]
    rows = [
        ["Data halls", f"{len(scene.by_kind('data_hall'))}", f"{area('data_hall'):,.0f}",
         f"{h['width_x']:.1f} x {h['depth_y']:.1f} m, {h['racks_provided']} racks, {h['density_kw_m2']} kW/m²"],
        ["Electrical rooms", f"{len(scene.by_kind('elec_room'))}", f"{area('elec_room'):,.0f}",
         f"on {scene.designs['core']['elec_levels']} levels"],
        ["STS / MV rooms", f"{len(scene.by_kind('sts_room')) + len(scene.by_kind('mv_room'))}",
         f"{area('sts_room') + area('mv_room'):,.0f}", ""],
        ["Support / admin", "1", f"{area('support') * s:,.0f}", f"{s} storey(s)"],
        ["Corridor", "1", f"{area('corridor') * s:,.0f}", ""],
        ["Mechanical gallery", "1", f"{area('mech_gallery') * s:,.0f}", "pipework, pumps"],
        ["GIA (approx.)", "", f"{scene.metrics['gia_m2']:,}", "excludes plant yards"],
    ]
    _heading(ax, 0.0, 1.0, "Area schedule")
    y = table(ax, 0.0, 0.955, ["Space", "No.", "Area m²", "Notes"], rows, [0.15, 0.05, 0.08, 0.27],
              fs=6.4, row_h=0.034, aligns=["left", "right", "right", "left"], bold_last=True)
    m = scene.metrics
    rows = [["Site area", f"{m['site_area_ha']} ha"], ["Building footprint", f"{m['footprint_m2']:,} m²"],
            ["Coverage", f"{m['coverage_pct']}%"], ["Roads", f"{m['road_area_m2']:,} m²"],
            ["Landscape buffer", f"{m['landscape_m2']:,} m²"], ["Spare land", f"~{m['spare_land_ha']} ha"],
            ["Building", f"{m['building_length_m']} x {m['building_depth_m']} m"]]
    _heading(ax, 0.0, y - 0.03, "Site")
    table(ax, 0.0, y - 0.075, ["Metric", "Value"], rows, [0.2, 0.15], fs=6.4, row_h=0.034)

    rows = [[("status", c.severity), c.message[:118]] for c in scene.checks]
    _heading(ax, 0.6, 1.0, "Rule checks")
    yy = table(ax, 0.6, 0.955, ["Result", "Check"], rows, [0.07, 0.33], fs=5.9, row_h=0.031,
               aligns=["left", "left"])
    _heading(ax, 0.6, yy - 0.03, "Engine log (decisions)")
    ty = yy - 0.075
    for line in scene.log[:10]:
        ax.text(0.6, ty, "- " + line[:120], fontsize=5.6, color=INK2, va="top", transform=ax.transAxes)
        ty -= 0.024
    return fig


def build_report(scene: Scene, options: list[Scene] | None, path: str, topo_rows: list[dict] | None = None):
    setup_fonts()
    options = options or [scene]
    figs = [sheet_options(scene, options), sheet_axo(scene, "site", -50, 32, "3D overview", "DC-002"),
            sheet_plan(scene), sheet_axo(scene, "core", 40, 30, "3D close-up", "DC-004")]
    if topo_rows:
        figs.append(sheet_electrical(scene, topo_rows))
    figs += [sheet_noise(scene), sheet_schedules(scene)]
    with PdfPages(path, metadata={"Title": f"{scene.brief.project} - test fit option {scene.option}",
                                  "Creator": "dcgen 0.1"}) as pdf:
        for f in figs:
            pdf.savefig(f)
            plt.close(f)
    return path
