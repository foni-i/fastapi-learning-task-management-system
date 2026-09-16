"""Version 1 product router composition."""

from fastapi import APIRouter

from app.api.v1.endpoints.agent_runs import router as agent_runs_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.knowledge_documents import (
    router as knowledge_documents_router,
)
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.users import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(agent_runs_router)
router.include_router(knowledge_documents_router)
router.include_router(users_router)
router.include_router(projects_router)
router.include_router(tasks_router)
