"""Quick PNG previews for agents and chat UIs: the site plan (failed/warned elements outlined) or a 3D view.
Same drawing code as the PDF sheets, minus the title block, so what the agent sees is what the PDF will show."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402

from ..schema import Scene  # noqa: E402
from .axo import draw_axo  # noqa: E402
from .plan import draw_labels, draw_north, draw_plan, draw_scalebar, plan_rotation  # noqa: E402
from .style import setup_fonts  # noqa: E402

VIEWS = ("plan", "overview", "closeup")


def flagged_ids(scene: Scene, limit: int = 40) -> list[str]:
    ids: list[str] = []
    for c in scene.checks:
        if c.severity in ("fail", "warn"):
            ids += [i for i in c.element_ids if i not in ids]
    return ids[:limit]


def preview_png(scene: Scene, view: str = "plan", px: int = 1400, highlight: list[str] | None = None) -> bytes:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    setup_fonts()
    w_in = 14.0
    fig = plt.figure(figsize=(w_in, 9.0), dpi=px / w_in)
    ax = fig.add_axes((0.01, 0.01, 0.98, 0.98))
    ax.axis("off")
    if view == "plan":
        draw_plan(ax, scene, margin=22, labels=False)
        draw_labels(ax, scene)
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        draw_north(ax, x1 - 14, y1 - 22, 10, 90)
        draw_scalebar(ax, x0 + 6, y0 + 6, 100)
        tr = Affine2D().rotate_deg(plan_rotation(scene, True)) + ax.transData
        ids = flagged_ids(scene) if highlight is None else highlight
        for e in scene.elements:
            if e.id in ids:
                ax.add_patch(Rectangle((e.x, e.y), max(e.w, 1.0), max(e.d, 1.0), fc="none", ec="#d62728", lw=1.6,
                                       zorder=30, transform=tr))
    else:
        draw_axo(ax, scene, az=-50 if view == "overview" else 40, el=32 if view == "overview" else 30,
                 focus="site" if view == "overview" else "core", fontsize=6.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
