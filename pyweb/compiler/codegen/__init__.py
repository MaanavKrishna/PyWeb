"""Codegen: app graph → HTML / JS / Python server + source maps."""

from .ir import AppGraph, build_graph
from .emit_js import emit_js
from .emit_html import emit_html, emit_page

__all__ = ["AppGraph", "build_graph", "emit_js", "emit_html", "emit_page"]
