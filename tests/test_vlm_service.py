import asyncio
import base64
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from app.agent.nodes import AgentNodes
from app.services import vlm_service as vlm_module
from app.services.vlm_service import VlmService
from tests.fake_ai import DeterministicVlmService


@pytest.fixture(autouse=True)
def allow_the_test_cdn(monkeypatch):
    """이 테스트들의 이미지는 우리 스토리지에서 온 것으로 둔다.

    VLM은 허용 목록에 있는 호스트에서만 이미지를 가져온다. 목록은 설정에서
    만들어지므로, 테스트가 쓰는 호스트를 공개 베이스 URL로 지정한다.
    """
    monkeypatch.setattr(vlm_module.settings, "public_base_url", "https://cdn.aidfit.com")


# Attributes as the model returns them, without the per-item verdict.
FASHION_ATTRS = {
    "name": "화이트 오버핏 니트",
    "brand": None,
    "category": "상의",
    "label": "니트",
    "gender": "unisex",
    "color": "White",
    "material": "Knit",
    "fit": "Oversized",
    "pattern": "solid",
    "mood": "Minimal",
    "sense_of_season": "spring",
}

NULL_ATTRS = {key: None for key in FASHION_ATTRS}

# Closet uploads use the single-item schema.
SINGLE_FASHION_RESULT = {"is_fashion_item": True, **FASHION_ATTRS}
SINGLE_NON_FASHION_RESULT = {"is_fashion_item": False, **NULL_ATTRS}


def multi_result(*items: dict, is_fashion_item: bool = True) -> dict:
    # Recommendation requests use the multi-item schema.
    return {"is_fashion_item": is_fashion_item, "items": list(items)}


MULTI_FASHION_RESULT = multi_result(FASHION_ATTRS)
MULTI_NON_FASHION_RESULT = multi_result(is_fashion_item=False)


class FakeResponse:
    def __init__(self, *, url: str, status_code: int = 200, content: bytes = b"", headers=None, payload=None):
        self.url = url
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._payload or {}

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and bool(
            self.headers.get("location")
        )

    async def aiter_bytes(self):
        # 실제 httpx처럼 조각으로 흘려준다. 서비스가 받으면서 크기를 자른다.
        yield self.content


class FakeAsyncClient:
    # Image bytes are the join key so responses stay correct under concurrency.
    images: dict[str, dict] = {}
    results: dict[bytes, object] = {}
    get_calls: list[str] = []
    post_calls: list[dict] = []
    timeouts: list = []
    client_options: list = []
    post_status: int = 200

    @classmethod
    def reset(cls) -> None:
        cls.images = {}
        cls.results = {}
        cls.get_calls = []
        cls.post_calls = []
        cls.timeouts = []
        cls.client_options = []
        cls.post_status = 200

    def __init__(self, timeout=None, **options) -> None:
        self.timeout = timeout
        self.options = options
        FakeAsyncClient.timeouts.append(timeout)
        FakeAsyncClient.client_options.append(options)

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str) -> FakeResponse:
        FakeAsyncClient.get_calls.append(url)
        spec = FakeAsyncClient.images.get(url)
        if spec is None:
            raise AssertionError(f"unregistered image url requested: {url}")
        return FakeResponse(
            url=url,
            status_code=spec["status"],
            content=spec["content"],
            headers={"content-type": spec["content_type"]},
        )

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs):
        # 서비스는 홉마다 검사하려고 stream + follow_redirects=False를 쓴다.
        yield await self.get(url)

    async def post(self, url: str, headers: dict, json: dict) -> FakeResponse:
        FakeAsyncClient.post_calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
        inline_data = json["contents"][0]["parts"][0]["inline_data"]
        image_bytes = base64.b64decode(inline_data["data"])
        result = FakeAsyncClient.results.get(image_bytes)
        if result is None:
            raise AssertionError("no model result registered for the posted image")

        if isinstance(result, dict) and "candidates" in result:
            payload = result
        else:
            text = result if isinstance(result, str) else _dump(result)
            payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return FakeResponse(url=url, status_code=FakeAsyncClient.post_status, payload=payload)


def _dump(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def register_image(url: str, model_result=None, *, content_type: str = "image/jpeg", content=None, status: int = 200) -> str:
    body = content if content is not None else f"binary::{url}".encode()
    FakeAsyncClient.images[url] = {"content": body, "content_type": content_type, "status": status}
    if model_result is not None:
        FakeAsyncClient.results[body] = model_result
    return url


def setup(monkeypatch, **overrides) -> None:
    FakeAsyncClient.reset()
    defaults = {
        "gemini_api_key": "test-key",
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta/",
        "gemini_model": "gemini-fallback",
        "vlm_model": "gemini-vision-test",
        "vlm_timeout_seconds": 12.0,
        "vlm_max_concurrency": 4,
        "vlm_max_image_bytes": 1024,
        "vlm_max_items_per_image": 8,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setattr(vlm_module.settings, key, value)
    monkeypatch.setattr(vlm_module.httpx, "AsyncClient", FakeAsyncClient)


def analyze(image_urls: list[str]) -> dict:
    # Recommendation path: one image may describe a whole outfit.
    return asyncio.run(VlmService().analyze_many(image_urls))


def analyze_single(image_url) -> dict:
    # Closet path: one photo is one garment.
    return asyncio.run(VlmService().analyze(image_url))


# ---------------------------------------------------------------------------
# Recommendation path: multi-item extraction
# ---------------------------------------------------------------------------


def test_single_garment_photo_returns_one_normalized_item(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)

    response = analyze([url])

    assert response["is_fashion_item"] is True
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["thumbnail_url"] == url
    assert item["is_fashion_item"] is True
    assert item["name"] == "화이트 오버핏 니트"
    assert item["category"] == "상의"
    # Term fields are lowercased so RAG filters and ranking compare cleanly.
    assert item["color"] == "white"
    assert item["material"] == "knit"
    assert item["fit"] == "oversized"
    assert item["mood"] == "minimal"
    assert item["sense_of_season"] == "spring"
    # An uploaded photo never carries a price or a product page.
    assert item["price"] is None
    assert item["product_url"] is None


def test_item_keys_match_the_vlm_contract(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)

    item = analyze([url])["items"][0]

    assert set(item) == {
        "name",
        "brand",
        "price",
        "category",
        "label",
        "gender",
        "thumbnail_url",
        "product_url",
        "color",
        "material",
        "fit",
        "pattern",
        "mood",
        "sense_of_season",
        "is_fashion_item",
    }


def test_outfit_photo_returns_one_item_per_garment(monkeypatch) -> None:
    # A full-body photo is the reason the recommendation path is multi-item.
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/outfit_001.jpg",
        multi_result(
            {**FASHION_ATTRS, "category": "아우터", "color": "blue", "material": "denim"},
            {**FASHION_ATTRS, "category": "바지", "color": "black", "material": "cotton"},
            {**FASHION_ATTRS, "category": "신발", "color": "black", "material": "leather"},
        ),
    )

    response = analyze([url])

    assert [item["category"] for item in response["items"]] == ["아우터", "바지", "신발"]
    assert [item["color"] for item in response["items"]] == ["blue", "black", "black"]
    # Every item points back at the one photo it came from.
    assert {item["thumbnail_url"] for item in response["items"]} == {url}
    # One image still means one API call.
    assert len(FakeAsyncClient.post_calls) == 1


def test_items_stay_grouped_by_source_image(monkeypatch) -> None:
    setup(monkeypatch)
    first = register_image(
        "https://cdn.aidfit.com/outfit_001.jpg",
        multi_result({**FASHION_ATTRS, "color": "white"}, {**FASHION_ATTRS, "color": "black"}),
    )
    second = register_image(
        "https://cdn.aidfit.com/outfit_002.jpg",
        multi_result({**FASHION_ATTRS, "color": "navy"}),
    )

    response = analyze([first, second])

    assert [item["color"] for item in response["items"]] == ["white", "black", "navy"]
    assert [item["thumbnail_url"] for item in response["items"]] == [first, first, second]


def test_items_per_image_are_capped(monkeypatch) -> None:
    # A busy image must not blow up the RAG request size.
    setup(monkeypatch, vlm_max_items_per_image=2)
    url = register_image(
        "https://cdn.aidfit.com/rack_001.jpg",
        multi_result(
            {**FASHION_ATTRS, "color": "white"},
            {**FASHION_ATTRS, "color": "black"},
            {**FASHION_ATTRS, "color": "navy"},
        ),
    )

    response = analyze([url])

    assert [item["color"] for item in response["items"]] == ["white", "black"]


def test_non_fashion_image_returns_one_null_placeholder(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/food_001.jpg", MULTI_NON_FASHION_RESULT)

    response = analyze([url])

    assert response["is_fashion_item"] is False
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["is_fashion_item"] is False
    assert item["thumbnail_url"] == url
    assert all(item[key] is None for key in item if key not in {"thumbnail_url", "is_fashion_item"})


def test_non_fashion_verdict_discards_any_listed_items(monkeypatch) -> None:
    # A poster of a shirt may still be listed; the verdict wins.
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/poster_001.jpg",
        multi_result(FASHION_ATTRS, is_fashion_item=False),
    )

    item = analyze([url])["items"][0]

    assert item["is_fashion_item"] is False
    assert item["color"] is None


def test_empty_item_list_counts_as_no_garment(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/empty_001.jpg", multi_result())

    response = analyze([url])

    assert response["is_fashion_item"] is False
    assert response["items"][0]["is_fashion_item"] is False


def test_any_non_fashion_image_invalidates_the_whole_request(monkeypatch) -> None:
    setup(monkeypatch)
    good = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)
    bad = register_image("https://cdn.aidfit.com/food_001.jpg", MULTI_NON_FASHION_RESULT)

    response = analyze([good, bad])

    assert response["is_fashion_item"] is False
    assert [item["is_fashion_item"] for item in response["items"]] == [True, False]


def test_items_must_be_a_list(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", {"is_fashion_item": True, "items": "nope"})

    with pytest.raises(RuntimeError, match="items must be a list"):
        analyze([url])


def test_missing_flag_is_reported_as_an_analysis_failure(monkeypatch) -> None:
    # A missing verdict is a malformed answer, not a "please re-upload" case.
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", {"items": [FASHION_ATTRS]})

    with pytest.raises(RuntimeError, match="missing is_fashion_item"):
        analyze([url])


def test_text_false_flag_is_not_treated_as_fashion(monkeypatch) -> None:
    # A model that answers with text must not turn a rejection into an acceptance.
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        {"is_fashion_item": "false", "items": [FASHION_ATTRS]},
    )

    response = analyze([url])

    assert response["is_fashion_item"] is False
    assert response["items"][0]["color"] is None


def test_text_true_flag_is_accepted(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        {"is_fashion_item": "true", "items": [FASHION_ATTRS]},
    )

    item = analyze([url])["items"][0]

    assert item["is_fashion_item"] is True
    assert item["color"] == "white"


def test_unknown_model_fields_are_dropped(monkeypatch) -> None:
    # VLMItem forbids extra keys, so drift must never reach the contract.
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        multi_result(
            {
                **FASHION_ATTRS,
                "confidence": 0.93,
                "bounding_box": [0, 0, 1, 1],
                "sub_category": "크루넥",
                "price": 41800,
                "product_url": "https://www.musinsa.com/products/1",
                "thumbnail_url": "https://hallucinated.example/other.jpg",
            }
        ),
    )

    item = analyze([url])["items"][0]

    assert "confidence" not in item
    assert "sub_category" not in item
    assert item["price"] is None
    assert item["product_url"] is None
    assert item["thumbnail_url"] == url


def test_placeholder_values_become_null(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        multi_result({**FASHION_ATTRS, "brand": "unknown", "pattern": "  ", "material": "N/A", "label": 12}),
    )

    item = analyze([url])["items"][0]

    assert item["brand"] is None
    assert item["pattern"] is None
    assert item["material"] is None
    assert item["label"] is None


def test_gender_and_season_synonyms_are_normalized(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        multi_result(
            {**FASHION_ATTRS, "gender": "MALE", "sense_of_season": "Autumn"},
            {**FASHION_ATTRS, "gender": "여성", "sense_of_season": "All Season"},
        ),
    )

    items = analyze([url])["items"]

    assert items[0]["gender"] == "men"
    assert items[0]["sense_of_season"] == "fall"
    assert items[1]["gender"] == "women"
    assert items[1]["sense_of_season"] == "all-season"


def test_markdown_wrapped_json_is_parsed(monkeypatch) -> None:
    setup(monkeypatch)
    wrapped = "```json\n" + _dump(MULTI_FASHION_RESULT) + "\n```"
    url = register_image("https://cdn.aidfit.com/item_001.jpg", wrapped)

    item = analyze([url])["items"][0]

    assert item["color"] == "white"


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_recommendation_request_asks_for_an_item_list(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.png", MULTI_FASHION_RESULT, content_type="image/png")

    analyze([url])

    call = FakeAsyncClient.post_calls[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-vision-test:generateContent"
    )
    assert call["headers"]["x-goog-api-key"] == "test-key"
    assert call["timeout"] == 12.0

    payload = call["json"]
    inline_data = payload["contents"][0]["parts"][0]["inline_data"]
    assert inline_data["mime_type"] == "image/png"
    assert base64.b64decode(inline_data["data"]) == FakeAsyncClient.images[url]["content"]

    schema = payload["generationConfig"]["responseSchema"]
    assert schema["properties"]["is_fashion_item"]["type"] == "BOOLEAN"
    assert schema["properties"]["items"]["type"] == "ARRAY"
    assert "sense_of_season" in schema["properties"]["items"]["items"]["properties"]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_closet_request_asks_for_a_single_item(monkeypatch) -> None:
    # The closet upload path must keep its one-photo-one-garment contract.
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", SINGLE_FASHION_RESULT)

    analyze_single(url)

    schema = FakeAsyncClient.post_calls[0]["json"]["generationConfig"]["responseSchema"]
    assert "items" not in schema["properties"]
    assert schema["properties"]["sense_of_season"]["type"] == "STRING"


def test_vlm_model_falls_back_to_the_shared_gemini_model(monkeypatch) -> None:
    setup(monkeypatch, vlm_model="")
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)

    analyze([url])

    assert FakeAsyncClient.post_calls[0]["url"].endswith("/models/gemini-fallback:generateContent")


def test_client_follows_redirects_and_sends_a_user_agent(monkeypatch) -> None:
    # Image CDNs redirect, and some hosts reject requests without a user agent.
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)

    analyze([url])

    options = FakeAsyncClient.client_options[0]
    assert options["follow_redirects"] is True
    assert options["headers"]["User-Agent"] == vlm_module.IMAGE_USER_AGENT


# ---------------------------------------------------------------------------
# Download and transport failures
# ---------------------------------------------------------------------------


def test_empty_image_urls_skip_the_network(monkeypatch) -> None:
    setup(monkeypatch)

    response = analyze([])

    assert response == {"items": [], "is_fashion_item": True}
    assert FakeAsyncClient.get_calls == []
    assert FakeAsyncClient.post_calls == []


def test_missing_api_key_raises_before_any_request(monkeypatch) -> None:
    setup(monkeypatch, gemini_api_key="")
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        analyze([url])
    assert FakeAsyncClient.get_calls == []


def test_non_http_image_url_raises(monkeypatch) -> None:
    setup(monkeypatch)

    with pytest.raises(ValueError, match="unsupported image url scheme"):
        analyze(["mock://no-image"])


def test_non_image_content_type_raises(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/page.html", MULTI_FASHION_RESULT, content_type="text/html")

    with pytest.raises(RuntimeError, match="did not return an image"):
        analyze([url])
    assert FakeAsyncClient.post_calls == []


def test_content_type_parameters_are_tolerated(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/item_001.jpg",
        MULTI_FASHION_RESULT,
        content_type="Image/JPEG; charset=binary",
    )

    item = analyze([url])["items"][0]

    assert item["is_fashion_item"] is True
    assert FakeAsyncClient.post_calls[0]["json"]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/jpeg"


def test_empty_image_body_raises(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/empty.jpg", content=b"")

    with pytest.raises(RuntimeError, match="empty body"):
        analyze([url])


def test_oversized_image_raises(monkeypatch) -> None:
    setup(monkeypatch, vlm_max_image_bytes=16)
    url = register_image("https://cdn.aidfit.com/big.jpg", MULTI_FASHION_RESULT, content=b"x" * 17)

    with pytest.raises(RuntimeError, match="larger than VLM_MAX_IMAGE_BYTES"):
        analyze([url])
    assert FakeAsyncClient.post_calls == []


def test_image_download_error_propagates(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/missing.jpg", MULTI_FASHION_RESULT, status=404)

    with pytest.raises(httpx.HTTPStatusError):
        analyze([url])


def test_gemini_http_error_propagates(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", MULTI_FASHION_RESULT)
    FakeAsyncClient.post_status = 500

    with pytest.raises(httpx.HTTPStatusError):
        analyze([url])


def test_gemini_without_candidates_raises(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", {"candidates": []})

    with pytest.raises(RuntimeError, match="no candidates"):
        analyze([url])


# ---------------------------------------------------------------------------
# Closet path and mock mode
# ---------------------------------------------------------------------------


def test_single_analyze_returns_one_flat_item(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/item_001.jpg", SINGLE_FASHION_RESULT)

    result = analyze_single(url)

    assert result["is_fashion_item"] is True
    assert result["thumbnail_url"] == url
    assert result["color"] == "white"


def test_single_analyze_rejects_a_non_fashion_photo(monkeypatch) -> None:
    setup(monkeypatch)
    url = register_image("https://cdn.aidfit.com/food_001.jpg", SINGLE_NON_FASHION_RESULT)

    result = analyze_single(url)

    assert result["is_fashion_item"] is False
    assert result["category"] is None


def test_single_analyze_without_image_skips_the_network(monkeypatch) -> None:
    setup(monkeypatch)

    result = analyze_single(None)

    assert result == {"thumbnail_url": "", "is_fashion_item": True}
    assert FakeAsyncClient.get_calls == []


def test_mock_mode_never_touches_the_network(monkeypatch) -> None:
    setup(monkeypatch)

    response = asyncio.run(DeterministicVlmService().analyze_many(["https://cdn.aidfit.com/item_001.jpg"]))

    assert response["items"][0]["color"] == "white"
    assert FakeAsyncClient.get_calls == []
    assert FakeAsyncClient.post_calls == []


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------


def test_agent_call_vlm_accepts_a_multi_item_response(monkeypatch) -> None:
    # The agent revalidates the payload, so this guards the real integration path.
    setup(monkeypatch)
    url = register_image(
        "https://cdn.aidfit.com/outfit_001.jpg",
        multi_result(
            {**FASHION_ATTRS, "category": "아우터"},
            {**FASHION_ATTRS, "category": "바지"},
        ),
    )
    nodes = AgentNodes(vlm_service=VlmService())

    response = asyncio.run(nodes.call_vlm([url]))

    assert response["is_fashion_item"] is True
    assert [item["category"] for item in response["items"]] == ["아우터", "바지"]


def test_agent_vlm_node_reports_failures_as_contract_errors(monkeypatch) -> None:
    setup(monkeypatch)
    nodes = AgentNodes(vlm_service=VlmService())
    state = {"image_urls": ["mock://no-image"], "has_image": True}

    result = asyncio.run(nodes.vlm_node(state))

    assert result["error"]["code"] == "VLM_ANALYSIS_FAILED"
    assert result["error"]["source"] == "vlm"
    assert result["error"]["retryable"] is True


class RedirectingFakeClient:
    """한 번 리다이렉트한 뒤 목적지 본문을 준다. 홉별 검사 확인용."""

    def __init__(self, location: str) -> None:
        self.location = location
        self.requested: list[str] = []

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs):
        self.requested.append(url)
        if len(self.requested) == 1:
            yield FakeResponse(url=url, status_code=302, headers={"location": self.location})
        else:
            yield FakeResponse(
                url=url, status_code=200, content=b"payload",
                headers={"content-type": "image/jpeg"},
            )


def download(client, url: str):
    return asyncio.run(VlmService()._download_image(client, url))


def test_a_host_we_did_not_hand_out_is_refused() -> None:
    """image_urls는 클라이언트가 넣는 값이다.

    검사하지 않으면 로그인한 사용자가 임의 주소를 넣어 서버가 대신 요청하게
    만들 수 있고, 응답 내용은 Gemini로 넘어간다.
    """
    with pytest.raises(ValueError, match="not allowed"):
        download(FakeAsyncClient(), "https://evil.example/photo.jpg")


def test_cloud_metadata_is_refused() -> None:
    # 서버리스·클라우드에서 자격증명이 새는 대표 경로다.
    with pytest.raises(ValueError, match="not allowed"):
        download(FakeAsyncClient(), "http://169.254.169.254/latest/meta-data/")


def test_internal_addresses_are_refused() -> None:
    for url in ("http://localhost:8000/admin", "http://10.0.0.5/", "http://[::1]/"):
        with pytest.raises(ValueError, match="not allowed"):
            download(FakeAsyncClient(), url)


def test_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="scheme"):
        download(FakeAsyncClient(), "file:///etc/passwd")


def test_a_redirect_off_the_allowed_host_is_refused() -> None:
    """신뢰하는 호스트가 다른 곳으로 넘겨도 따라가지 않는다.

    허용 검사를 첫 주소에만 하면 리다이렉트 한 번으로 우회된다.
    """
    client = RedirectingFakeClient("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(ValueError, match="not allowed"):
        download(client, "https://cdn.aidfit.com/item_001.jpg")

    assert len(client.requested) == 1, "차단된 목적지로 요청이 나가면 안 된다"


def test_a_redirect_within_the_allowed_host_is_followed() -> None:
    # 스토리지는 서명된 주소로 넘기는 일이 있다. 같은 호스트면 따라간다.
    client = RedirectingFakeClient("https://cdn.aidfit.com/real.jpg")

    content, mime_type = download(client, "https://cdn.aidfit.com/item_001.jpg")

    assert content == b"payload"
    assert mime_type == "image/jpeg"


def test_a_redirect_loop_gives_up() -> None:
    class LoopingClient(RedirectingFakeClient):
        @asynccontextmanager
        async def stream(self, method: str, url: str, **kwargs):
            self.requested.append(url)
            yield FakeResponse(url=url, status_code=302, headers={"location": self.location})

    client = LoopingClient("https://cdn.aidfit.com/again.jpg")

    with pytest.raises(RuntimeError, match="redirected too many times"):
        download(client, "https://cdn.aidfit.com/item_001.jpg")
