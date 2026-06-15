from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

CODEX_HOME = Path(os.getenv("CODEX_HOME", "/codex-home"))
WORKSPACE_DIR = Path(os.getenv("CODEX_WORKSPACE", "/workspace"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("CODEX_TIMEOUT_SECONDS", "900"))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "100000"))
DEFAULT_SANDBOX = os.getenv("CODEX_SANDBOX", "read-only")
MAX_CONCURRENT_CODEX = int(os.getenv("MAX_CONCURRENT_CODEX", "1"))
WRAPPER_API_KEY = os.getenv("WRAPPER_API_KEY")

ALLOWED_SANDBOXES = {"read-only", "workspace-write"}

app = FastAPI(title="Codex CLI API", version="0.1.0")
codex_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CODEX)


class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str | None = Field(
        default=None,
        max_length=128,
        description="Optional Codex model override passed as --model.",
    )
    sandbox: Literal["read-only", "workspace-write"] | None = Field(
        default=None,
        description="Per-request sandbox override. danger-full-access is intentionally not exposed.",
    )
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class PromptResponse(BaseModel):
    response: str
    elapsed_seconds: float
    sandbox: str
    model: str | None = None


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if WRAPPER_API_KEY and x_api_key != WRAPPER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {
        "ok": True,
        "codex_binary": shutil.which("codex"),
        "codex_home": str(CODEX_HOME),
        "codex_config_present": (CODEX_HOME / "config.toml").exists(),
        "codex_auth_json_present": (CODEX_HOME / "auth.json").exists(),
    }


@app.post("/v1/prompt", response_model=PromptResponse, dependencies=[Depends(require_api_key)])
async def run_prompt(request: PromptRequest) -> PromptResponse:
    async with codex_semaphore:
        return await asyncio.to_thread(_run_codex, request)


def _run_codex(request: PromptRequest) -> PromptResponse:
    start = time.monotonic()

    codex_path = shutil.which("codex")
    if not codex_path:
        raise HTTPException(status_code=500, detail="codex binary was not found in PATH.")

    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    sandbox = request.sandbox or DEFAULT_SANDBOX
    if sandbox not in ALLOWED_SANDBOXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sandbox {sandbox!r}. Allowed: {sorted(ALLOWED_SANDBOXES)}",
        )

    timeout = request.timeout_seconds or DEFAULT_TIMEOUT_SECONDS

    with tempfile.TemporaryDirectory(prefix="codex-api-") as tmpdir:
        output_file = Path(tmpdir) / "last-message.txt"
        args = [
            codex_path,
            "exec",
            "--color",
            "never",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "--output-last-message",
            str(output_file),
        ]

        if request.model:
            args.extend(["--model", request.model])

        # Read the prompt from stdin to avoid shell quoting issues and argv-size limits.
        args.append("-")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(CODEX_HOME)
        env["CODEX_WORKSPACE"] = str(WORKSPACE_DIR)
        env.setdefault("RUST_LOG", "error")

        try:
            completed = subprocess.run(
                args,
                input=request.prompt,
                text=True,
                capture_output=True,
                cwd=str(WORKSPACE_DIR),
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={
                    "message": "codex exec timed out.",
                    "timeout_seconds": timeout,
                    "stdout_tail": _tail(exc.stdout),
                    "stderr_tail": _tail(exc.stderr),
                },
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""

        if completed.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "codex exec failed.",
                    "returncode": completed.returncode,
                    "stdout_tail": _tail(stdout),
                    "stderr_tail": _tail(stderr),
                },
            )

        if output_file.exists():
            response = output_file.read_text(encoding="utf-8", errors="replace")
        else:
            response = stdout

    return PromptResponse(
        response=response.strip(),
        elapsed_seconds=round(time.monotonic() - start, 3),
        sandbox=sandbox,
        model=request.model,
    )


def _tail(value: str | bytes | None, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:]
