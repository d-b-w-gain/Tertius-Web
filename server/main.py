import asyncio
import os
import sys
from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure the workflows directory is in the Python path so we can import them
sys.path.append(str(Path(__file__).parent))

from core.auth import AuthContext, get_auth_context
from core.config import get_settings

# Import the individual FastAPI apps
from workflows.intus.intus_server import app as intus_app
from workflows.artus.artus_server import app as artus_app
from workflows.extus.extus_server import app as extus_app
from workflows.timus.timus_server import app as timus_app
from workflows.intus.compile_result_consumer import run_result_consumer

settings = get_settings()

# Create the master Monolith app
app = FastAPI(title="Tertius Monolith API")
_compile_result_stop_event: asyncio.Event | None = None
_compile_result_task: asyncio.Task | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Tertius Backend is running"}


@app.get("/api/me")
def read_me(ctx: AuthContext = Depends(get_auth_context)):
    return {
        "user_id": str(ctx.user_id),
        "tenant_id": str(ctx.tenant_id),
        "email": ctx.email,
    }


@app.on_event("startup")
async def start_compile_result_consumer():
    global _compile_result_stop_event, _compile_result_task
    _compile_result_stop_event = asyncio.Event()
    _compile_result_task = asyncio.create_task(run_result_consumer(_compile_result_stop_event))


@app.on_event("shutdown")
async def stop_compile_result_consumer():
    if _compile_result_stop_event is not None:
        _compile_result_stop_event.set()
    if _compile_result_task is not None:
        _compile_result_task.cancel()
        try:
            await _compile_result_task
        except asyncio.CancelledError:
            pass


# Mount the workflows to sub-paths
app.mount("/api/intus", intus_app)
app.mount("/api/artus", artus_app)
app.mount("/api/extus", extus_app)
app.mount("/api/timus", timus_app)

if __name__ == "__main__":
    import uvicorn
    # Use environment variable for port, default to 8000
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
