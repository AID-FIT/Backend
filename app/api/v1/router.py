from fastapi import APIRouter

from app.api.v1 import (
    auth,
    chats,
    closet,
    cron,
    feedback,
    health,
    images,
    likes,
    products,
    recommendations,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(images.router, prefix="/images", tags=["images"])
api_router.include_router(closet.router, prefix="/closet", tags=["closet"])
api_router.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(likes.router, prefix="/likes", tags=["likes"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(chats.router, prefix="/chats", tags=["chats"])
api_router.include_router(cron.router, prefix="/cron", tags=["cron"])
