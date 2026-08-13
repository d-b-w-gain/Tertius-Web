"""One mechanical definition; Tertius derives every workbench projection."""

import build123d as bd

from lysaght_zc import CONNECTION_FAMILY, cee_member
from structural_connections import bolted_fixed_base


purlin = cee_member(
    "C10019",
    start_mm=(0, 0, 0),
    end_mm=(0, 0, 2400),
    mark="P1",
    role="purlin",
)
base = bolted_fixed_base(
    purlin,
    port_name="start",
    connection_family=CONNECTION_FAMILY,
    mark="BASE1",
)
model = bd.Compound(  # type: ignore[call-overload]
    children=[purlin, base],
    label="Lysaght Cee cantilever demonstration",
)
