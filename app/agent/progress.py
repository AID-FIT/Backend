"""에이전트 노드를 사용자가 읽을 진행 문구로 옮긴다.

추천 한 건은 13초쯤 걸린다. 그동안 화면에는 스켈레톤만 떠서, 동작 중인지
멈춘 건지 구분할 수 없었다. 여기서 만드는 문구는 **실제 상태에서 읽은 값**을
함께 싣는다. "찾는 중"만 반복하면 추정치와 다를 게 없다.
"""

from typing import Any

# 카탈로그 규모. 문구에 실어 "어디서 고르고 있는지"를 알린다.
CATALOG_SIZE = 12_794

# 사용자에게 노출하지 않는 노드. 밀리초 단위라 깜빡이기만 하고,
# "요청을 검사했어요" 같은 문구는 아무것도 알려주지 않는다.
_SILENT_NODES = frozenset(
    {"input_validation", "context_check", "fashion_item_check", "build_rag_request"}
)

_RETRIEVAL_TARGET_LABELS = {
    "closet": "내 옷장",
    "musinsa": "무신사 카탈로그",
    "hybrid": "내 옷장과 무신사 카탈로그",
}


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def describe_step(node_name: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """노드 하나가 끝났을 때 보낼 진행 이벤트. 노출하지 않을 노드면 None."""
    if node_name in _SILENT_NODES:
        return None

    label, detail = _label_and_detail(node_name, state)
    if label is None:
        return None
    return {"node": node_name, "label": label, "detail": detail}


def _label_and_detail(node_name: str, state: dict[str, Any]) -> tuple[str | None, str | None]:
    if node_name == "intent_classifier":
        return "무엇을 찾는지 파악했어요", None

    if node_name == "vlm":
        count = _count(state.get("vlm_items"))
        return "사진 속 옷을 살펴봤어요", f"{count}벌 인식" if count else None

    if node_name == "query_refiner":
        refined = str(state.get("rag_query") or state.get("resolved_query") or "").strip()
        # 질의 전체를 그대로 보여주면 취향 문장이 길어 화면을 덮는다.
        return "검색어를 다듬었어요", _shorten(refined)

    if node_name == "retrieval_planner":
        target = _RETRIEVAL_TARGET_LABELS.get(str(state.get("retrieval_target") or ""))
        return "어디서 찾을지 정했어요", target

    if node_name in {"closet_rag", "musinsa_rag", "hybrid_rag", "fallback_search"}:
        found = _count(state.get("rag_results"))
        where = "내 옷장" if node_name == "closet_rag" else f"상품 {CATALOG_SIZE:,}건"
        if node_name == "fallback_search":
            # 첫 검색이 비었을 때만 도는 노드다. 그 사실을 숨기면 결과가
            # 요청과 다른 이유를 사용자가 알 수 없다.
            return "조건을 넓혀 다시 찾았어요", f"후보 {found}건"
        return f"{where}에서 골랐어요", f"후보 {found}건"

    if node_name == "reuse_rag_results":
        return "직전 검색 결과를 다시 썼어요", f"후보 {_count(state.get('ranked_items'))}건"

    if node_name == "style_ranker":
        ranked = state.get("ranked_items")
        kinds = len({str(item.get("category") or "") for item in ranked}) if isinstance(ranked, list) else 0
        return "취향에 맞게 순서를 매겼어요", f"{kinds}가지 종류" if kinds else None

    if node_name == "final_response":
        return "추천 이유를 정리했어요", None

    if node_name == "general_chat_response":
        return "답변을 작성했어요", None

    if node_name == "error_response":
        error = state.get("error") or {}
        return "요청을 마무리하지 못했어요", str(error.get("message") or "") or None

    return None, None


def _shorten(value: str, limit: int = 40) -> str | None:
    if not value:
        return None
    return value if len(value) <= limit else f"{value[:limit].rstrip()}…"
