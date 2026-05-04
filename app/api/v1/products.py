from fastapi import APIRouter, HTTPException

from app.schemas.product import ProductResponse
from app.services.rag_service import RagService

router = APIRouter()


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str) -> ProductResponse:
    products = await RagService().search(product_id)
    for product in products:
        if product["id"] == product_id:
            return ProductResponse(
                id=product["id"],
                brand=product["brand"],
                name=product["name"],
                category=product["category"],
                price=product.get("price"),
                image_url=product.get("image_url"),
                tags=product.get("tags", []),
            )
    raise HTTPException(status_code=404, detail="Product not found")

