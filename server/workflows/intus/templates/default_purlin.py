"""One mechanical definition; Tertius derives every workbench projection."""

import build123d as bd

from lysaght_zc import CONNECTION_FAMILY, cee_member
from structural_connections import bolted_fixed_base, bolted_rigid_knee


column = cee_member(
    "C20024",
    start_mm=(0, 0, 0),
    end_mm=(0, 0, 1200),
    mark="C1",
    role="column",
    rotation_deg=90,
)
purlin = cee_member(
    "C10019",
    start_mm=(0, 0, 1200),
    end_mm=(1200, 0, 1200),
    mark="P1",
    role="purlin",
)
base = bolted_fixed_base(
    column,
    port_name="start",
    connection_family=CONNECTION_FAMILY,
    mark="BASE1",
)
knee = bolted_rigid_knee(
    column,
    purlin,
    first_port_name="end",
    second_port_name="start",
    connection_family=CONNECTION_FAMILY,
    mark="KNEE1",
)
model = bd.Compound(  # type: ignore[call-overload]
    children=[column, purlin, base, knee],
    label="Lysaght Cee knee-frame demonstration",
)
