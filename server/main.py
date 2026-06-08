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

settings = get_settings()

# Create the master Monolith app
app = FastAPI(title="Tertius Monolith API")

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
