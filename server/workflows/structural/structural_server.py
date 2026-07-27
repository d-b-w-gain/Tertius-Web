from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import Response

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.structural.cantilever_fixture import cantilever_glb, cantilever_snapshot
from core.structural.contracts import StructuralSnapshot

app = FastAPI(title="Tertius Structural Design Workbench")


@app.get("/fixture/cantilever", response_model=StructuralSnapshot)
def get_cantilever_fixture(
    _ctx: AuthContext = Depends(get_auth_context),
) -> StructuralSnapshot:
    return cantilever_snapshot()


@app.get("/fixture/cantilever/model")
def get_cantilever_model(
    _ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    return Response(content=cantilever_glb(), media_type="model/gltf-binary")

