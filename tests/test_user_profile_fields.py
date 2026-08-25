import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.nodes import AgentNodes
from app.core.gender import normalize_gender
from app.db.models import UserPreference
from app.schemas.recommendation import UserProfile
from app.schemas.user import UserPreferenceUpdate
from app.services.user_service import UserService, to_agent_profile


class FakeFlushDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


class StubUserService(UserService):
    def __init__(self, preference: UserPreference | None) -> None:
        self.preference = preference

    async def get_preference(self, db: object, user: object) -> UserPreference | None:
        return self.preference


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("남성", "men"),
        ("여자", "women"),
        ("MALE", "men"),
        ("Women's", "women"),
        ("남녀공용", "unisex"),
        ("men", "men"),
        ("  ", None),
        (None, None),
    ],
)
def test_gender_input_is_normalized_to_the_catalog_vocabulary(raw, expected) -> None:
    # 카탈로그(product_vectors.gender)는 men/women/unisex만 쓴다. 화면이 뭘 보내든
    # 경계에서 한 번 맞춰야 필터가 빗나가지 않는다.
    assert normalize_gender(raw) == expected


def test_unknown_gender_is_rejected_rather_than_dropped() -> None:
    # 조용히 None으로 삼키면 사용자가 성별을 골랐는데도 필터가 안 걸린다.
    with pytest.raises(ValueError):
        normalize_gender("기타")

    with pytest.raises(ValidationError):
        UserPreferenceUpdate(gender="기타")


@pytest.mark.parametrize("height", [99, 251, -1])
def test_impossible_height_is_rejected(height) -> None:
    with pytest.raises(ValidationError):
        UserPreferenceUpdate(height_cm=height)


@pytest.mark.parametrize("height", [100, 175, 250])
def test_plausible_height_is_accepted(height) -> None:
    assert UserPreferenceUpdate(height_cm=height).height_cm == height


def test_agent_profile_carries_gender_and_height() -> None:
    preference = SimpleNamespace(
        styles=["minimal"],
        sizes={"age_range": "20대"},
        gender="men",
        height_cm=178,
    )

    assert to_agent_profile(preference) == {
        "age_group": "20대",
        "preferred_styles": ["minimal"],
        "gender": "men",
        "height_cm": 178,
    }


def test_agent_profile_survives_a_user_with_no_preferences_yet() -> None:
    assert to_agent_profile(None) == {
        "age_group": None,
        "preferred_styles": [],
        "gender": None,
        "height_cm": None,
    }


def test_agent_profile_contract_accepts_the_new_fields() -> None:
    # UserProfile은 extra="forbid"라, 필드를 열어두지 않으면 POST /recommendations가 422가 된다.
    profile = UserProfile(**to_agent_profile(SimpleNamespace(
        styles=[], sizes={}, gender="women", height_cm=162,
    )))

    assert profile.gender == "women"
    assert profile.height_cm == 162


def test_saving_a_profile_without_gender_keeps_the_stored_one() -> None:
    # 이 PATCH는 나머지 필드를 통째로 교체한다. 두 필드를 모르는 구버전 앱이
    # 프로필을 저장할 때마다 성별과 키가 지워지면 안 된다.
    preference = UserPreference(user_id="user_001", gender="men", height_cm=178)
    service = StubUserService(preference)

    asyncio.run(
        service.upsert_preference(
            FakeFlushDb(),
            SimpleNamespace(id="user_001"),
            UserPreferenceUpdate(styles=["캐주얼"]),
        )
    )

    assert preference.gender == "men"
    assert preference.height_cm == 178
    assert preference.styles == ["캐주얼"]


def test_sending_gender_explicitly_overwrites_it() -> None:
    preference = UserPreference(user_id="user_001", gender="men", height_cm=178)
    service = StubUserService(preference)

    asyncio.run(
        service.upsert_preference(
            FakeFlushDb(),
            SimpleNamespace(id="user_001"),
            UserPreferenceUpdate(styles=[], gender="여성", height_cm=162),
        )
    )

    assert preference.gender == "women"
    assert preference.height_cm == 162


def test_clearing_gender_explicitly_is_honoured() -> None:
    preference = UserPreference(user_id="user_001", gender="men", height_cm=178)
    service = StubUserService(preference)

    asyncio.run(
        service.upsert_preference(
            FakeFlushDb(),
            SimpleNamespace(id="user_001"),
            UserPreferenceUpdate(styles=[], gender=None, height_cm=None),
        )
    )

    assert preference.gender is None
    assert preference.height_cm is None


def test_a_first_time_profile_stores_both_fields() -> None:
    db = FakeFlushDb()
    service = StubUserService(None)

    preference = asyncio.run(
        service.upsert_preference(
            db,
            SimpleNamespace(id="user_001"),
            UserPreferenceUpdate(styles=["미니멀"], gender="남성", height_cm=181),
        )
    )

    assert db.added == [preference]
    assert (preference.gender, preference.height_cm) == ("men", 181)


def test_profile_gender_becomes_a_search_filter() -> None:
    filters = AgentNodes()._build_rag_filters({}, {"gender": "men"}, [], query="바지 추천해줘")

    assert filters["gender"] == "men"


def test_unisex_is_not_a_filter() -> None:
    # unisex는 "가리지 않는다"는 뜻이다. 조건으로 걸면 남녀 상품이 전부 빠진다.
    filters = AgentNodes()._build_rag_filters({}, {"gender": "unisex"}, [], query="바지 추천해줘")

    assert "gender" not in filters


def test_an_explicit_request_beats_the_profile() -> None:
    filters = AgentNodes()._build_rag_filters(
        {"gender": "women"}, {"gender": "men"}, [], query="바지 추천해줘"
    )

    assert filters["gender"] == "women"
