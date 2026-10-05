"""Codegen support: the app graph (PIR) used by ``pyweb inspect``.

Browser JavaScript is emitted by :mod:`pyweb.compiler.lower`; HTML is
rendered by :mod:`pyweb.ssr`.
"""

from .ir import AppGraph, build_graph

__all__ = ["AppGraph", "build_graph"]
