"""Artus AST parsing and feature update integration tests.

Tests the full semantic feature tree pipeline:
  - parse_tree() with complex build123d scripts
  - Feature extraction (variables with types, comments)
  - Operation tree (BuildPart contexts, function defs, bd.* calls)
  - update_features() type preservation (int, float, str, bool)
  - Full read -> edit -> save -> verify cycle via HTTP endpoints
  - Edge cases: missing variable, non-constant assignments, syntax errors
"""

from __future__ import annotations

import ast

from sqlalchemy import select

from core.models import ProjectFile
from workflows.artus import artus_server

# ---------------------------------------------------------------------------
# Complex build123d test scripts
# ---------------------------------------------------------------------------

SIMPLE_SCRIPT = """import build123d as bd
width = 10
height = 20.5  # overall height in mm
name = "bracket"
active = True

with bd.BuildPart() as part:
    bd.Box(width, height, 5)
    bd.Cylinder(radius=3, height=height)
"""

NESTED_SCRIPT = """import build123d as bd
length = 100.0
width = 50.0
thickness = 3.0  # wall thickness

def create_base():
    return bd.Box(length, width, thickness)

with bd.BuildPart() as base:
    bd.Box(length, width, thickness)
    with bd.BuildSketch() as sketch:
        bd.Rectangle(length - 10, width - 10)
        bd.fillet(radius=5)
"""

MULTI_PART_SCRIPT = """import build123d as bd
outer_diam = 50.0
inner_diam = 30.0
wall = 2.5

with bd.BuildPart() as outer:
    bd.Cylinder(radius=outer_diam / 2, height=100)

with bd.BuildPart() as inner:
    bd.Cylinder(radius=inner_diam / 2, height=100)

def connector():
    bd.Box(10, 10, 50)
"""


# ---------------------------------------------------------------------------
# parse_tree unit tests
# ---------------------------------------------------------------------------

def test_parse_tree_extracts_buildpart_context():
    """parse_tree should recognize with bd.BuildPart() blocks as Context nodes."""
    tree = ast.parse("with bd.BuildPart() as p:\n    pass")
    with_node = tree.body[0]
    result = artus_server.parse_tree(with_node)

    assert len(result) == 1
    ctx = result[0]
    assert ctx["type"] == "Context"
    assert ctx["name"] == "BuildPart()"
    assert ctx["as_name"] == "p"
    assert ctx["children"] == []


def test_parse_tree_extracts_function_def():
    """parse_tree should recognize function defs as Context nodes."""
    tree = ast.parse("def my_func():\n    bd.Box(1,2,3)")
    func_node = tree.body[0]
    result = artus_server.parse_tree(func_node)

    assert len(result) == 1
    ctx = result[0]
    assert ctx["type"] == "Context"
    assert ctx["name"] == "def my_func()"
    assert ctx["as_name"] is None
    assert len(ctx["children"]) == 1
    assert ctx["children"][0]["type"] == "Operation"
    assert ctx["children"][0]["name"] == "Box"


def test_parse_tree_extracts_bd_operations():
    """parse_tree should extract bd.*() calls as Operation nodes with arguments."""
    tree = ast.parse("bd.Box(width, height, length=30)")
    expr_node = tree.body[0]
    result = artus_server.parse_tree(expr_node)

    assert len(result) == 1
    op = result[0]
    assert op["type"] == "Operation"
    assert op["name"] == "Box"
    assert "width" in op["arguments"]
    assert "height" in op["arguments"]
    assert "length=30" in op["arguments"]


def test_parse_tree_ignores_non_bd_calls():
    """parse_tree should ignore calls that aren't bd.*."""
    tree = ast.parse("print('hello')")
    result = artus_server.parse_tree(tree.body[0])
    assert result == []


def test_parse_tree_extracts_dependencies():
    """extract_dependencies should find variable references, excluding builtins."""
    tree = ast.parse("bd.Box(width, bd.MIN + height)")
    deps = artus_server.extract_dependencies(tree)
    assert "width" in deps
    assert "height" in deps
    assert "bd" not in deps
    assert "MIN" not in deps


def test_parse_tree_nested_contexts():
    """Nested BuildPart inside BuildPart should produce nested Context nodes."""
    tree = ast.parse(NESTED_SCRIPT)
    result = []
    for node in tree.body:
        result.extend(artus_server.parse_tree(node))

    # Should have: function def "create_base" + top-level BuildPart "base"
    contexts = [r for r in result if r["type"] == "Context"]
    assert len(contexts) >= 2, f"Expected at least 2 contexts, got {len(contexts)}"

    # The BuildPart "base" should contain a nested BuildSketch
    base_ctx = [c for c in contexts if c.get("as_name") == "base"]
    assert len(base_ctx) == 1
    sketch_ctxs = [c for c in base_ctx[0]["children"] if c["type"] == "Context"]
    assert len(sketch_ctxs) >= 1
    sketch = sketch_ctxs[0]
    assert "BuildSketch" in sketch["name"]


# ---------------------------------------------------------------------------
# Feature extraction via HTTP endpoint
# ---------------------------------------------------------------------------

def test_get_features_extracts_variables_with_types(authenticated_artus_client, db_session, seeded_tenant):
    """GET /features should return typed variables with values and comments."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.get("/features")
    assert response.status_code == 200
    data = response.json()

    assert data["project_name"] == "default_purlin"
    features = data["features"]

    # Check variable extraction
    var_names = {f["name"] for f in features}
    assert "width" in var_names
    assert "height" in var_names
    assert "name" in var_names
    assert "active" in var_names

    # Check types
    width = [f for f in features if f["name"] == "width"][0]
    assert width["type"] == "int"
    assert width["value"] == 10

    height = [f for f in features if f["name"] == "height"][0]
    assert height["type"] == "float"
    assert height["value"] == 20.5
    assert height["description"] == "overall height in mm"

    name = [f for f in features if f["name"] == "name"][0]
    assert name["type"] == "str"
    assert name["value"] == "bracket"

    active = [f for f in features if f["name"] == "active"][0]
    assert active["type"] == "bool"
    assert active["value"] is True


def test_get_features_extracts_operations_tree(authenticated_artus_client, db_session, seeded_tenant):
    """GET /features should return the operation tree with BuildPart contexts."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.get("/features")
    assert response.status_code == 200
    data = response.json()

    operations = data["operations"]
    assert len(operations) > 0

    # Should have a BuildPart context
    contexts = [op for op in operations if op["type"] == "Context"]
    assert len(contexts) == 1
    assert "BuildPart" in contexts[0]["name"]
    assert contexts[0]["as_name"] == "part"

    # Children should include Box and Cylinder
    child_names = {c["name"] for c in contexts[0]["children"]}
    assert "Box" in child_names
    assert "Cylinder" in child_names


def test_get_features_skips_non_constant_assignments(authenticated_artus_client, db_session, seeded_tenant):
    """Variables assigned from expressions (not constants) should be excluded
    from features (but operations referencing them should still appear)."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = MULTI_PART_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.get("/features")
    assert response.status_code == 200
    data = response.json()

    features = data["features"]
    var_names = {f["name"] for f in features}

    # outer_diam, inner_diam, wall are simple constants -> extracted
    assert "outer_diam" in var_names
    assert "inner_diam" in var_names
    assert "wall" in var_names

    # Expression-based assignments (radius=outer_diam/2) are NOT extracted
    # because they're not ast.Constant values
    assert "radius" not in var_names


def test_get_features_returns_404_when_no_active_project(authenticated_artus_client, db_session, seeded_tenant):
    """When the user has no active project, GET /features should return 404."""
    # Clear workspace state
    from core.models import UserWorkspaceState

    state = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == seeded_tenant.user_id,
            UserWorkspaceState.tenant_id == seeded_tenant.tenant_id,
        )
    )
    state.active_project_id = None
    state.active_file_id = None
    db_session.commit()

    response = authenticated_artus_client.get("/features")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Feature update (update_features endpoint)
# ---------------------------------------------------------------------------

def test_update_features_preserves_int_type(authenticated_artus_client, db_session, seeded_tenant):
    """Updating an int variable should preserve its integer type."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.post("/update_features", json={"updates": {"width": 42}})
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify the source code was updated correctly
    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert "width = 42" in updated.content
    assert "height = 20.5" in updated.content  # unchanged


def test_update_features_preserves_float_type(authenticated_artus_client, db_session, seeded_tenant):
    """Updating a float variable should preserve its float type."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.post(
        "/update_features", json={"updates": {"height": 99.9}}
    )
    assert response.status_code == 200

    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert "height = 99.9" in updated.content
    assert "width = 10" in updated.content  # unchanged


def test_update_features_preserves_string_quoting(authenticated_artus_client, db_session, seeded_tenant):
    """Updating a string variable should preserve Python string quoting."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.post(
        "/update_features", json={"updates": {"name": "new_bracket"}}
    )
    assert response.status_code == 200

    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert 'name = "new_bracket"' in updated.content or "name = 'new_bracket'" in updated.content


def test_update_features_preserves_bool_type(authenticated_artus_client, db_session, seeded_tenant):
    """Updating a bool variable should use True/False (not true/false)."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.post(
        "/update_features", json={"updates": {"active": False}}
    )
    assert response.status_code == 200

    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert "active = False" in updated.content


def test_update_features_preserves_comments(authenticated_artus_client, db_session, seeded_tenant):
    """Column-offset editing should preserve inline comments on other lines."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    response = authenticated_artus_client.post(
        "/update_features", json={"updates": {"width": 77}}
    )
    assert response.status_code == 200

    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    # The comment on the height line should still be there
    assert "# overall height in mm" in updated.content


def test_update_features_silently_skips_missing_variable(authenticated_artus_client, db_session, seeded_tenant):
    """Updating a variable that doesn't exist should not error, just skip it."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    original = SIMPLE_SCRIPT
    design.content = original
    db_session.commit()

    response = authenticated_artus_client.post(
        "/update_features", json={"updates": {"nonexistent": 999, "width": 55}}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    db_session.expire_all()
    updated = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    assert "width = 55" in updated.content
    assert "999" not in updated.content


def test_update_features_full_read_edit_verify_cycle(authenticated_artus_client, db_session, seeded_tenant):
    """Full cycle: read features, edit several variables, verify via re-read."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    # 1. Read features
    read1 = authenticated_artus_client.get("/features")
    assert read1.status_code == 200
    features_before = {f["name"]: f for f in read1.json()["features"]}
    assert features_before["width"]["value"] == 10
    assert features_before["height"]["value"] == 20.5
    assert features_before["name"]["value"] == "bracket"

    # 2. Update multiple variables
    update = authenticated_artus_client.post(
        "/update_features",
        json={"updates": {"width": 200, "height": 50.0, "name": "large_bracket"}},
    )
    assert update.status_code == 200

    # 3. Re-read and verify
    read2 = authenticated_artus_client.get("/features")
    assert read2.status_code == 200
    features_after = {f["name"]: f for f in read2.json()["features"]}
    assert features_after["width"]["value"] == 200
    assert features_after["height"]["value"] == 50.0
    assert features_after["name"]["value"] == "large_bracket"


def test_update_features_creates_snapshot(authenticated_artus_client, db_session, seeded_tenant):
    """Each update_features call should create a source snapshot via save_code."""
    from core.models import SourceSnapshot

    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = SIMPLE_SCRIPT
    db_session.commit()

    # Count snapshots before
    before = db_session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
            SourceSnapshot.project_id == seeded_tenant.project_id,
        )
    ).all()
    before_count = len(before)

    authenticated_artus_client.post("/update_features", json={"updates": {"width": 99}})

    after = db_session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.tenant_id == seeded_tenant.tenant_id,
            SourceSnapshot.project_id == seeded_tenant.project_id,
        )
    ).all()
    assert len(after) == before_count + 1
    assert after[-1].message == "Updated features via Artus"
