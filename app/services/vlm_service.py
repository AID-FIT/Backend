import asyncio
import base64
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.schemas.ai import VLMResponse
from app.services.gemini_client import extract_text, parse_json_object


# Only remote images can be inlined into a vision request.
ALLOWED_IMAGE_SCHEMES = {"http", "https"}

# Some image hosts block requests that do not identify themselves.
IMAGE_USER_AGENT = "AID-FIT-VLM/1.0 (+https://github.com/AID-FIT)"

# Free-form descriptive fields that keep their original language.
TEXT_FIELDS = ("name", "brand", "category", "label")

# Fields that RAG filtering and style ranking compare as plain terms.
TERM_FIELDS = ("gender", "color", "material", "fit", "pattern", "mood", "sense_of_season")

# Values that mean "the model does not know" and should stay null.
PLACEHOLDER_TERMS = {"unknown", "n/a", "na", "none", "null", "nan", "미상", "알수없음", "알 수 없음"}

SEASON_SYNONYMS = {
    "autumn": "fall",
    "all season": "all-season",
    "allseason": "all-season",
    "all-seasons": "all-season",
    "four-season": "all-season",
    "봄": "spring",
    "여름": "summer",
    "가을": "fall",
    "겨울": "winter",
    "사계절": "all-season",
}

GENDER_SYNONYMS = {
    "male": "men",
    "man": "men",
    "men's": "men",
    "mens": "men",
    "남성": "men",
    "남자": "men",
    "female": "women",
    "woman": "women",
    "women's": "women",
    "womens": "women",
    "여성": "women",
    "여자": "women",
    "uni": "unisex",
    "both": "unisex",
    "공용": "unisex",
    "남녀공용": "unisex",
}

_SHARED_RULES = (
    "Set is_fashion_item to false when the image holds no wearable fashion item "
    "(landscape, food, pet, screenshot, face-only selfie, and so on). "
    "Describe only what is visible and never guess a brand, a price, or a product page. "
    "Write name, category, and label in Korean. "
    "Write gender, color, material, fit, pattern, mood, and sense_of_season in lowercase English."
)

# Closet uploads stay one photo per garment, so that path asks for a single item.
VLM_SYSTEM_INSTRUCTION = (
    "You are AID-FIT's fashion vision analyst. "
    "Each image contains at most one clothing or fashion item, so describe exactly one item. "
    "Return only one valid JSON object matching the provided response schema. Do not use markdown. "
    "When is_fashion_item is false, set every other field to null. " + _SHARED_RULES
)

# Recommendation requests accept outfit photos, so that path lists every worn item.
VLM_MULTI_SYSTEM_INSTRUCTION = (
    "You are AID-FIT's fashion vision analyst. "
    "List every distinct wearable item visible in the image as its own entry: "
    "tops, bottoms, outerwear, dresses, shoes, bags, hats, and legwear. "
    "One entry per garment. Never merge two garments into one entry and never split one garment into two. "
    "Ignore the person, the background, and anything that is not worn. "
    "Return only one valid JSON object matching the provided response schema. Do not use markdown. "
    "When is_fashion_item is false, return an empty items list. " + _SHARED_RULES
)

_FIELD_RULES = (
    "- name: short Korean item name, e.g. 화이트 오버핏 니트\n"
    "- brand: only when a logo is clearly legible, otherwise null\n"
    "- category: one Korean top-level category, e.g. 상의, 바지, 스커트, 원피스, 아우터, 신발, 가방, 모자, 양말\n"
    "- label: Korean item type, e.g. 니트, 데님 팬츠, 볼캡\n"
    "- gender: men, women, or unisex\n"
    "- color: dominant color, e.g. white, black, navy, beige\n"
    "- material: e.g. cotton, denim, knit, leather, nylon\n"
    "- fit: oversized, regular, slim, wide, or semi-wide. "
    "Judge it by the shoulder line and body width, never by sleeve length.\n"
    "- pattern: e.g. solid, stripe, check, graphic, logo\n"
    "- mood: e.g. minimal, casual, street, sporty, formal\n"
    "- sense_of_season: exactly one of spring, summer, fall, winter, all-season. "
    "Decide from fabric weight first, never from sleeve length: fleece, wool, corduroy, padding "
    "and heavy knit are fall or winter even when short-sleeved, while linen and thin cotton are summer. "
    "Use all-season when the fabric suits the whole year.\n"
)

VLM_USER_PROMPT = "Analyze this image and fill the response schema.\n" + _FIELD_RULES

VLM_MULTI_USER_PROMPT = (
    "Analyze this image and list each worn item separately, using these rules for every entry.\n" + _FIELD_RULES
)


class VlmService:
    def __init__(self, use_mock_ai: bool | None = None) -> None:
        self.use_mock_ai = settings.use_mock_ai if use_mock_ai is None else use_mock_ai

    async def analyze(self, image_url: str | None) -> dict:
        # Closet uploads stay one garment per photo, so this path asks for a single item.
        response = await self._analyze_batch([image_url] if image_url else [], multi_item=False)
        if response["items"]:
            item = response["items"][0]
            return {**item, "is_fashion_item": response["is_fashion_item"]}
        return {
            "thumbnail_url": image_url or "mock://no-image",
            "is_fashion_item": True,
        }

    async def analyze_many(self, image_urls: list[str]) -> dict:
        # Recommendation requests accept outfit photos, so one image may yield several items.
        return await self._analyze_batch(image_urls, multi_item=True)

    async def _analyze_batch(self, image_urls: list[str], multi_item: bool) -> dict:
        # Mock mode is the default until the real vision service is connected.
        if self.use_mock_ai:
            return await self._mock_analyze_many(image_urls)
        return await self._external_analyze_many(image_urls, multi_item)

    async def _mock_analyze_many(self, image_urls: list[str]) -> dict:
        items = [self._mock_item(image_url) for image_url in image_urls]
        response = {
            "items": items,
            "is_fashion_item": all(bool(item.get("is_fashion_item")) for item in items) if items else True,
        }
        return VLMResponse.model_validate(response).model_dump()

    async def _external_analyze_many(self, image_urls: list[str], multi_item: bool = True) -> dict:
        if not image_urls:
            return VLMResponse().model_dump()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        semaphore = asyncio.Semaphore(max(1, int(settings.vlm_max_concurrency)))

        async def analyze_one(client: httpx.AsyncClient, image_url: str) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._analyze_image(client, image_url, multi_item)

        # Image hosts commonly redirect, and some reject requests without a user agent.
        client_options = {
            "timeout": settings.vlm_timeout_seconds,
            "follow_redirects": True,
            "headers": {"User-Agent": IMAGE_USER_AGENT},
        }
        async with httpx.AsyncClient(**client_options) as client:
            # gather preserves order, so items stay grouped by their source image.
            per_image = list(await asyncio.gather(*(analyze_one(client, url) for url in image_urls)))

        response = {
            "items": [item for image_items in per_image for item in image_items],
            # Every image must yield a garment before the agent may continue to retrieval.
            "is_fashion_item": all(
                any(item["is_fashion_item"] for item in image_items) for image_items in per_image
            ),
        }
        return VLMResponse.model_validate(response).model_dump()

    async def _analyze_image(
        self,
        client: httpx.AsyncClient,
        image_url: str,
        multi_item: bool,
    ) -> list[dict[str, Any]]:
        image_bytes, mime_type = await self._download_image(client, image_url)
        url = f"{settings.gemini_base_url.rstrip('/')}/models/{settings.vlm_model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }

        payload = self._build_payload(image_bytes, mime_type, multi_item)
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()

        parsed = parse_json_object(extract_text(response.json()))
        if not multi_item:
            return [self._normalize_item(parsed, image_url)]
        return self._normalize_items(parsed, image_url)

    def _normalize_items(self, parsed: dict[str, Any], image_url: str) -> list[dict[str, Any]]:
        # An outfit photo answers with one entry per worn item.
        if "is_fashion_item" not in parsed:
            raise RuntimeError("VLM response is missing is_fashion_item")

        raw_items = parsed.get("items")
        if not isinstance(raw_items, list):
            raise RuntimeError("VLM response items must be a list")

        if not self._as_bool(parsed["is_fashion_item"]):
            # Keep one placeholder entry so the source image stays visible to the agent.
            return [self._normalize_item({"is_fashion_item": False}, image_url)]

        limit = max(1, int(settings.vlm_max_items_per_image))
        items = [
            self._normalize_item({**item, "is_fashion_item": True}, image_url)
            for item in raw_items[:limit]
            if isinstance(item, dict)
        ]
        if not items:
            # The verdict says fashion but nothing was listed, so treat it as no item found.
            return [self._normalize_item({"is_fashion_item": False}, image_url)]
        return items

    async def _download_image(self, client: httpx.AsyncClient, image_url: str) -> tuple[bytes, str]:
        # generateContent cannot fetch remote URLs, so the bytes are inlined here.
        scheme = urlparse(image_url).scheme.lower()
        if scheme not in ALLOWED_IMAGE_SCHEMES:
            raise ValueError(f"unsupported image url scheme: {image_url}")

        response = await client.get(image_url)
        response.raise_for_status()

        mime_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not mime_type.startswith("image/"):
            raise RuntimeError(f"image url did not return an image: {image_url}")

        content = response.content
        if not content:
            raise RuntimeError(f"image url returned an empty body: {image_url}")
        if len(content) > int(settings.vlm_max_image_bytes):
            raise RuntimeError(f"image is larger than VLM_MAX_IMAGE_BYTES: {image_url}")
        return content, mime_type

    def _build_payload(self, image_bytes: bytes, mime_type: str, multi_item: bool = False) -> dict[str, Any]:
        system_instruction = VLM_MULTI_SYSTEM_INSTRUCTION if multi_item else VLM_SYSTEM_INSTRUCTION
        user_prompt = VLM_MULTI_USER_PROMPT if multi_item else VLM_USER_PROMPT
        return {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                        {"text": user_prompt},
                    ],
                }
            ],
            "generationConfig": {
                # Gemini 3 models degrade when temperature drops below the default,
                # so the response schema alone keeps the output deterministic enough.
                "responseMimeType": "application/json",
                "responseSchema": self._response_schema(multi_item),
            },
        }

    def _response_schema(self, multi_item: bool = False) -> dict[str, Any]:
        if multi_item:
            return {
                "type": "OBJECT",
                "properties": {
                    "is_fashion_item": {"type": "BOOLEAN"},
                    "items": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": self._item_properties(),
                            "required": list(self._item_properties()),
                        },
                    },
                },
                "required": ["is_fashion_item", "items"],
            }

        properties = self._item_properties()
        return {
            "type": "OBJECT",
            "properties": {"is_fashion_item": {"type": "BOOLEAN"}, **properties},
            "required": ["is_fashion_item", *properties],
        }

    def _item_properties(self) -> dict[str, Any]:
        # Every described field is a nullable string; the flag lives outside this map.
        return {key: {"type": "STRING", "nullable": True} for key in TEXT_FIELDS + TERM_FIELDS}

    def _normalize_item(self, parsed: dict[str, Any], image_url: str) -> dict[str, Any]:
        # Build the item from known keys only so model drift cannot break VLMItem.
        if "is_fashion_item" not in parsed:
            # A missing verdict is a malformed answer, not a rejected image.
            raise RuntimeError("VLM response is missing is_fashion_item")

        is_fashion_item = self._as_bool(parsed["is_fashion_item"])
        item: dict[str, Any] = {key: None for key in TEXT_FIELDS + TERM_FIELDS}
        item.update(
            {
                # An uploaded photo carries no trustworthy price or product page.
                "price": None,
                "product_url": None,
                "thumbnail_url": image_url,
                "is_fashion_item": is_fashion_item,
            }
        )
        if not is_fashion_item:
            # Non-garment images keep only the source URL, per the VLM contract.
            return item

        for key in TEXT_FIELDS:
            item[key] = self._clean_text(parsed.get(key))
        for key in TERM_FIELDS:
            item[key] = self._clean_term(parsed.get(key))

        item["gender"] = GENDER_SYNONYMS.get(item["gender"], item["gender"])
        item["sense_of_season"] = SEASON_SYNONYMS.get(item["sense_of_season"], item["sense_of_season"])
        return item

    def _as_bool(self, value: Any) -> bool:
        # Some models answer the boolean flag as text, and "false" must stay false.
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"", "false", "0", "no", "n", "null", "none"}
        return bool(value)

    def _clean_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in PLACEHOLDER_TERMS:
            return None
        return cleaned

    def _clean_term(self, value: Any) -> str | None:
        cleaned = self._clean_text(value)
        return cleaned.lower() if cleaned else None

    def _mock_item(self, image_url: str) -> dict:
        # Mark obvious non-fashion test URLs as invalid fashion inputs.
        lowered = image_url.lower()
        is_fashion_item = not any(word in lowered for word in ["food", "cat", "dog", "car", "landscape"])
        return {
            "name": "업로드 의류 이미지",
            "brand": "unknown",
            "price": None,
            "category": "상의",
            "label": "니트",
            "gender": "unisex",
            "thumbnail_url": image_url,
            "product_url": None,
            "color": "white",
            "material": "cotton",
            "fit": "oversized",
            "pattern": "solid",
            "mood": "casual",
            "sense_of_season": "spring",
            "is_fashion_item": is_fashion_item,
        }
