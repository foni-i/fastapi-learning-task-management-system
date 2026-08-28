"""Version 1 product router composition."""

from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router

router = APIRouter()
router.include_router(auth_router)
