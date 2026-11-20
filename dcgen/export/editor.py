"""Standalone drag editor (single HTML file, no dependencies). The same page is
served as an MCP App (ui://dcgen/editor.html) by the MCP server."""
from __future__ import annotations

from pathlib import Path

from ..schema import Scene

TEMPLATE = Path(__file__).with_name("editor.html")


def editor_html(scene: Scene | None = None) -> str:
    html = TEMPLATE.read_text()
    data = scene.model_dump_json() if scene is not None else "null"
    data = data.replace("</", "<\\/")          # keep the JSON from closing the script tag
    return html.replace("/*SCENE_JSON*/null", data)


def export_editor(scene: Scene, path: str) -> str:
    Path(path).write_text(editor_html(scene))
    return path
