"""
API v1 Router - mounts all v1 endpoints.
"""

from fastapi import APIRouter

from app.api.v1.alerts import router as alerts_router
from app.api.v1.credentials import router as credentials_router
from app.api.v1.executions import router as executions_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.monitors import router as monitors_router
from app.api.v1.workspaces import router as workspaces_router

api_router = APIRouter()

# Include all routers
api_router.include_router(workspaces_router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(integrations_router, prefix="/integrations", tags=["integrations"])
api_router.include_router(monitors_router, prefix="/monitors", tags=["monitors"])
api_router.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
api_router.include_router(executions_router, prefix="/executions", tags=["executions"])
api_router.include_router(credentials_router, prefix="/credentials", tags=["credentials"])
