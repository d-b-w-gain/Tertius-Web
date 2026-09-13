from __future__ import annotations


def install_build123d_compatibility() -> None:
    """Install the OCP compatibility required by the supported Build123D runtime."""

    from OCP.TopoDS import TopoDS_Shape

    if not hasattr(TopoDS_Shape, "HashCode"):

        def _topods_shape_hash_code(self, upper_bound: int) -> int:
            return hash(self) % upper_bound

        TopoDS_Shape.HashCode = _topods_shape_hash_code  # type: ignore[attr-defined]
