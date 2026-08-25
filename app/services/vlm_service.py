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
# 리다이렉트는 따라가되 홉마다 다시 검사한다. 신뢰하는 호스트가 다른 곳으로
# 넘겨도 그 목적지가 허용 목록에 없으면 가지 않는다.
MAX_IMAGE_REDIRECTS = 3

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
    async def analyze(self, image_url: str | None) -> dict:
        # Closet uploads stay one garment per photo, so this path asks for a single item.
        response = await self._analyze_batch([image_url] if image_url else [], multi_item=False)
        if response["items"]:
            item = response["items"][0]
            return {**item, "is_fashion_item": response["is_fashion_item"]}
        return {
            "thumbnail_url": image_url or "",
            "is_fashion_item": True,
        }

    async def analyze_many(self, image_urls: list[str]) -> dict:
        # Recommendation requests accept outfit photos, so one image may yield several items.
        return await self._analyze_batch(image_urls, multi_item=True)

    async def _analyze_batch(self, image_urls: list[str], multi_item: bool) -> dict:
        # Mock mode is the default until the real vision service is connected.
        return await self._external_analyze_many(image_urls, multi_item)

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

    @staticmethod
    def _allowed_image_hosts() -> set[str]:
        """이미지를 가져와도 되는 호스트.

        우리가 내준 주소(스토리지·공개 베이스 URL)만 담는다. 설정에서 만들므로
        환경마다 다르고, 로컬 개발은 PUBLIC_BASE_URL이 가리키는 곳이 열린다.
        """
        hosts = set()
        for candidate in (settings.supabase_url, settings.public_base_url):
            host = urlparse(str(candidate or "")).hostname
            if host:
                hosts.add(host.lower())
        return hosts

    def _assert_image_url_allowed(self, image_url: str) -> None:
        """클라이언트가 준 주소를 서버가 그대로 가져오지 않도록 막는다.

        이 주소는 요청 본문(`image_urls`)으로 들어온다. 검사하지 않으면
        로그인한 사용자가 클라우드 메타데이터(169.254.169.254)나 사내망 주소를
        넣어 서버가 대신 요청하게 만들 수 있다. 응답 내용은 Gemini로 넘어가므로
        유출 경로까지 열린다. 스킴만 보는 것으로는 부족하다 — 호스트를 본다.
        """
        parsed = urlparse(image_url)
        if parsed.scheme.lower() not in ALLOWED_IMAGE_SCHEMES:
            raise ValueError(f"unsupported image url scheme: {image_url}")

        host = (parsed.hostname or "").lower()
        allowed = self._allowed_image_hosts()
        if not host or host not in allowed:
            # 어디로 가려 했는지는 남기되 응답에는 싣지 않는다.
            raise ValueError(f"image host is not allowed: {host or '(없음)'}")

    async def _download_image(self, client: httpx.AsyncClient, image_url: str) -> tuple[bytes, str]:
        # generateContent cannot fetch remote URLs, so the bytes are inlined here.
        url = image_url
        for _ in range(MAX_IMAGE_REDIRECTS + 1):
            self._assert_image_url_allowed(url)
            async with client.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError(f"image url redirected without a target: {url}")
                    url = str(httpx.URL(url).join(location))
                    continue

                response.raise_for_status()
                mime_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                if not mime_type.startswith("image/"):
                    raise RuntimeError(f"image url did not return an image: {image_url}")

                limit = int(settings.vlm_max_image_bytes)
                chunks: list[bytes] = []
                size = 0
                # 다 받은 뒤에 크기를 재면 상한을 넘는 응답이 이미 메모리에 올라온다.
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise RuntimeError(f"image is larger than VLM_MAX_IMAGE_BYTES: {image_url}")
                    chunks.append(chunk)

                content = b"".join(chunks)
                if not content:
                    raise RuntimeError(f"image url returned an empty body: {image_url}")
                return content, mime_type

        raise RuntimeError(f"image url redirected too many times: {image_url}")

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
