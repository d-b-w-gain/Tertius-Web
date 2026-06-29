"""Deterministic procurement analysis helpers.

This package intentionally has no web, database, Kubernetes, or UI
dependencies. It is the testable core that turns design source metadata and
GLTF assembly structure into procurement analysis artifacts.
"""

from .requirements import build_procurement_analysis
from .source_analysis import analyze_design_sources
from .visual_tree import analyze_gltf_tree

__all__ = [
    "analyze_design_sources",
    "analyze_gltf_tree",
    "build_procurement_analysis",
]
