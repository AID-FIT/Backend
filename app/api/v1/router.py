from fastapi import APIRouter

from app.api.v1 import auth, feedback, health, images, products, recommendations, users

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(images.router, prefix="/images", tags=["images"])
api_router.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])

