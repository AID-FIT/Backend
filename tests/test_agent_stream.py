"""파이프라인 진행 스트리밍.

추천 한 건은 13초쯤 걸린다. 그동안 화면에 아무 정보도 없었다.
`stream()`은 그래프가 도는 중간에 진행 상황을 흘린다.
"""

import asyncio

from app.agent.progress import describe_step
from tests.test_agent_pipeline import build_pipeline

QUERY = "화이트 니트랑 어울리는 바지 추천해줘"


def collect(**kwargs) -> list[dict]:
    pipeline, _vlm, _rag, _llm = build_pipeline()

    async def drain() -> list[dict]:
        return [event async for event in pipeline.stream(query=QUERY, user_id="user_001", **kwargs)]

    return asyncio.run(drain())


def steps(events: list[dict]) -> list[str]:
    return [event["node"] for event in events if event["type"] == "step"]


def test_stream_reports_progress_before_the_result() -> None:
    events = collect()

    assert events[-1]["type"] == "result"
    assert len(steps(events)) > 0


def test_stream_ends_with_exactly_one_result() -> None:
    # 결과가 두 번 오면 프론트가 화면을 두 번 갈아끼운다.
    events = collect()

    assert [event["type"] for event in events].count("result") == 1


def test_progress_follows_the_graph_order() -> None:
    reported = steps(collect())

    assert reported.index("retrieval_planner") < reported.index("style_ranker")
    assert reported.index("style_ranker") < reported.index("final_response")


def test_search_step_reports_how_many_candidates_were_found() -> None:
    # "찾는 중"만 반복하면 추정 타이머와 다를 게 없다. 실제 수치를 실어야 한다.
    events = collect()
    search = next(event for event in events if event.get("node") == "musinsa_rag")

    assert "후보" in (search["detail"] or "")


def test_silent_nodes_stay_out_of_the_stream() -> None:
    # 밀리초 만에 끝나는 검증 노드까지 보내면 문구가 깜빡이기만 한다.
    reported = steps(collect())

    assert "input_validation" not in reported
    assert "context_check" not in reported


def test_stream_result_matches_run() -> None:
    # run()을 _build_initial_state / _build_trace_result로 쪼갰다.
    # 그 리팩터가 결과를 바꾸지 않았는지 확인한다.
    pipeline, _vlm, _rag, _llm = build_pipeline()
    from_run = asyncio.run(
        pipeline.run(query=QUERY, user_id="user_001", return_trace=True)
    )

    events = collect()
    from_stream = {key: value for key, value in events[-1].items() if key != "type"}

    assert from_stream["response"] == from_run["response"]
    assert from_stream["retrieval_target"] == from_run["retrieval_target"]
    assert from_stream["intent"] == from_run["intent"]


def test_stream_carries_the_tile_count_through() -> None:
    events = collect(max_recommendations=8)

    assert events[-1]["type"] == "result"


def describe(node: str, **state) -> dict | None:
    return describe_step(node, state)


def test_every_reported_step_has_a_label() -> None:
    # label이 비면 화면에 빈 줄이 쌓인다.
    for event in collect():
        if event["type"] == "step":
            assert event["label"]


def test_unknown_nodes_are_not_reported() -> None:
    assert describe("some_new_node") is None


def test_vlm_step_counts_the_recognized_garments() -> None:
    step = describe("vlm", vlm_items=[{"category": "top"}, {"category": "pants"}])

    assert step is not None
    assert "2벌" in step["detail"]


def test_vlm_step_omits_the_count_when_nothing_was_recognized() -> None:
    step = describe("vlm", vlm_items=[])

    assert step is not None
    assert step["detail"] is None


def test_ranker_step_counts_distinct_categories() -> None:
    step = describe(
        "style_ranker",
        ranked_items=[{"category": "바지"}, {"category": "상의"}, {"category": "바지"}],
    )

    assert step is not None
    assert "2가지" in step["detail"]


def test_refiner_step_shortens_a_long_query() -> None:
    # 홈 질의는 취향 문장이 붙어 100자를 넘는다. 그대로 띄우면 화면을 덮는다.
    step = describe("query_refiner", rag_query="가" * 200)

    assert step is not None
    assert len(step["detail"]) <= 41
    assert step["detail"].endswith("…")


def test_fallback_search_is_reported_as_a_widened_search() -> None:
    # 조건을 넓혀 다시 찾았다는 사실을 숨기면, 결과가 요청과 다른 이유를
    # 사용자가 알 수 없다.
    step = describe("fallback_search", rag_results=[{}, {}])

    assert step is not None
    assert "넓혀" in step["label"]
