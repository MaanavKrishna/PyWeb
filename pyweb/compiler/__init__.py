"""PyWeb compiler pipeline: parse → analyze → place → RPC → codegen."""

from .pipeline import compile_source

__all__ = ["compile_source"]
