import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.llm_service import LlmService


SAMPLE_VLM_ITEMS = [
    {
        "category": "상의",
        "color": "white",
        "material": "knit",
        "fit": "oversized",
        "mood": "minimal",
        "sense_of_season": "spring",
    }
]


SAMPLE_RANKED_ITEMS = [
    {
        "item_id": "6081171",
        "source": "musinsa",
        "name": "스트레이트 데님 팬츠",
        "brand": "Example Denim",
        "category": "바지",
        "image_url": "https://image.msscdn.net/images/no_image_500.png",
        "product_url": "https://www.musinsa.com/products/6081171",
        "price": 59000,
        "color": "blue",
        "material": "denim",
        "fit": "straight",
        "pattern": "solid",
        "mood": "casual",
        "sense_of_season": "spring",
        "final_score": 0.9,
    },
    {
        "item_id": "6103287",
        "source": "musinsa",
        "name": "블랙 와이드 슬랙스",
        "brand": "Example Formal",
        "category": "바지",
        "image_url": "https://image.msscdn.net/images/no_image_500.png",
        "product_url": "https://www.musinsa.com/products/6103287",
        "price": 69000,
        "color": "black",
        "material": "polyester",
        "fit": "wide",
        "pattern": "solid",
        "mood": "minimal",
        "sense_of_season": "spring",
        "final_score": 0.82,
    },
]


SAMPLE_CLOSET_ITEMS = [
    {
        "closet_item_id": "closet_001",
        "category": "가방",
        "color": "black",
        "material": "pvc",
        "fit": "none",
        "pattern": "solid",
        "mood": "minimal",
        "sense_of_season": "all-season",
    }
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the LLM recommendation step.")
    parser.add_argument(
        "--query",
        default="화이트 오버핏 니트에 어울리는 바지 추천해줘",
        help="User request passed directly to the LLM service.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock LLM path instead of calling Gemini.",
    )
    args = parser.parse_args()

    service = LlmService(use_mock_ai=args.mock)
    result = await service.compose_recommendation(
        query=args.query,
        vlm_items=SAMPLE_VLM_ITEMS,
        ranked_items=SAMPLE_RANKED_ITEMS,
        retrieval_target="musinsa",
        closet_items=SAMPLE_CLOSET_ITEMS,
        use_closet_style=True,
        user_profile={"age_group": "20s", "preferred_styles": ["minimal", "casual"]},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
