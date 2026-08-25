from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.like import (
    DEFAULT_LIKE_PAGE_SIZE,
    MAX_LIKE_PAGE_SIZE,
    LikedRefsResponse,
    ProductLikeCreate,
    ProductLikeResponse,
)
from app.services.like_service import LikeService, ProductNotIdentifiableError

router = APIRouter()


@router.put("", response_model=ProductLikeResponse)
async def like_product(
    payload: ProductLikeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductLikeResponse:
    # PUT이다. 같은 요청을 여러 번 보내도 결과가 같아야 하트가 어긋나지 않는다.
    try:
        like = await LikeService().like(db=db, user_id=current_user.id, payload=payload)
    except ProductNotIdentifiableError:
        raise HTTPException(
            status_code=422, detail="item_id, product_url, image_url 중 하나는 있어야 합니다"
        ) from None

    return ProductLikeResponse.model_validate(like)


@router.delete("", status_code=204)
async def unlike_product(
    product_ref: str = Query(min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # 이미 꺼져 있어도 204다. 두 번 눌렀다고 오류를 보여줄 이유가 없다.
    await LikeService().unlike(db=db, user_id=current_user.id, product_ref=product_ref)
    return Response(status_code=204)


@router.get("", response_model=list[ProductLikeResponse])
async def list_likes(
    limit: int = Query(default=DEFAULT_LIKE_PAGE_SIZE, ge=1, le=MAX_LIKE_PAGE_SIZE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProductLikeResponse]:
    likes = await LikeService().list_for_user(db=db, user_id=current_user.id, limit=limit)
    return [ProductLikeResponse.model_validate(like) for like in likes]


@router.get("/refs", response_model=LikedRefsResponse)
async def list_liked_refs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LikedRefsResponse:
    # 하트를 채울지만 판단하면 되므로 상품 정보 없이 식별자만 돌려준다.
    refs = await LikeService().list_refs(db=db, user_id=current_user.id)
    return LikedRefsResponse(product_refs=refs)
