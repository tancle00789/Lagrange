from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from pk_agent.agentos.dependencies import enforce_rate_limit
from pk_agent.modelblocks.artifacts import ArtifactNotFoundError
from pk_agent.security.tenant import Principal

router = APIRouter()
artifacts_router = APIRouter()

_CONTENT_TYPES = {
    ".json": "application/json",
    ".joblib": "application/octet-stream",
    ".pt": "application/octet-stream",
}


@router.get("")
async def list_model_blocks(
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> list[dict]:
    registry = getattr(request.app.state, "model_block_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="model-block subsystem unavailable")
    return registry.catalog_with_schemas()


@artifacts_router.get("/{artifact_id:path}")
async def get_artifact(
    artifact_id: str,
    request: Request,
    principal: Principal = Depends(enforce_rate_limit),
) -> Response:
    # Artifact keys are namespaced `{tenant_id}/{task_id}/{run}-{file}`; the
    # tenant prefix is the authorization boundary.
    if not artifact_id.startswith(f"{principal.tenant_id}/") or ".." in artifact_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="artifact store unavailable")
    try:
        data = await store.get(artifact_id)
    except ArtifactNotFoundError:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    except ValueError:
        raise HTTPException(status_code=404, detail="artifact not found") from None

    suffix = "." + artifact_id.rsplit(".", 1)[-1] if "." in artifact_id else ""
    content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
    filename = artifact_id.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
