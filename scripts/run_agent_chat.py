"""Interactive local chat runner for the AID-FIT agent pipeline.

This entry point intentionally bypasses FastAPI, the database, and the frontend.
It keeps only in-memory conversation and retrieval context for the current process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# LangGraph 1.0.5 emits a dependency-transition warning on import that is not
# actionable for this runner and otherwise obscures the first chat response.
from langchain_core._api.deprecation import suppress_langchain_deprecation_warning

with suppress_langchain_deprecation_warning():
    from app.agent.agent_pipeline import AidFitAgentPipeline
    from app.agent.nodes import AgentNodes
    from app.core.config import settings
    from app.services.llm_service import LlmService
    from app.services.rag_service import RagService
    from app.services.vlm_service import VlmService


SUPPORTED_MODES = ("auto", "gemini")
DEFAULT_HISTORY_MESSAGES = 20


class LocalChatConfigurationError(RuntimeError):
    """Raised when the selected local runner mode cannot be started."""


class LocalChatSession:
    """Keep the same lightweight context that the persistent chat service uses."""

    def __init__(
        self,
        pipeline: AidFitAgentPipeline,
        *,
        user_id: str = "local-powershell",
        history_limit: int = DEFAULT_HISTORY_MESSAGES,
    ) -> None:
        self.pipeline = pipeline
        self.user_id = user_id
        self.history_limit = max(history_limit, 2)
        self.chat_history: list[dict[str, str]] = []
        self.previous_rag_results: list[dict[str, Any]] = []
        self.previous_shown_item_refs: list[str] = []
        self.previous_rag_query: str | None = None
        self.previous_retrieval_target: str | None = None

    async def send(
        self,
        query: str,
        *,
        image_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("질문을 입력해주세요.")

        with suppress_langchain_deprecation_warning():
            trace = await self.pipeline.run(
                query=normalized_query,
                user_id=self.user_id,
                image_urls=image_urls or [],
                chat_history=list(self.chat_history),
                previous_rag_results=list(self.previous_rag_results),
                previous_shown_item_refs=list(self.previous_shown_item_refs),
                previous_rag_query=self.previous_rag_query,
                previous_retrieval_target=self.previous_retrieval_target,
                return_trace=True,
            )
        response = trace.get("response")
        if not isinstance(response, dict):
            raise RuntimeError("에이전트가 올바른 응답 객체를 반환하지 않았습니다.")

        assistant_message = str(response.get("message") or "").strip()
        self.chat_history.extend(
            [
                {"role": "user", "content": normalized_query},
                {"role": "assistant", "content": assistant_message or "응답이 비어 있습니다."},
            ]
        )
        self.chat_history = self.chat_history[-self.history_limit :]

        candidate_pool = trace.get("candidate_pool")
        if not isinstance(candidate_pool, list):
            candidate_pool = trace.get("rag_items")
        if isinstance(candidate_pool, list) and candidate_pool:
            self.previous_rag_results = [item for item in candidate_pool if isinstance(item, dict)]
            shown_item_refs = trace.get("shown_item_refs")
            if isinstance(shown_item_refs, list):
                self.previous_shown_item_refs = list(
                    dict.fromkeys(str(item_ref) for item_ref in shown_item_refs if str(item_ref))
                )
            if not trace.get("rag_reused"):
                self.previous_rag_query = trace.get("rag_query") or trace.get("resolved_query")
            self.previous_retrieval_target = trace.get("retrieval_target")

        return trace

    def reset(self) -> None:
        self.chat_history.clear()
        self.previous_rag_results.clear()
        self.previous_shown_item_refs.clear()
        self.previous_rag_query = None
        self.previous_retrieval_target = None


def resolve_mode(requested_mode: str) -> str:
    # 목업 경로가 사라져 남은 모드는 gemini 하나뿐이다.
    return "gemini" if requested_mode == "auto" else requested_mode


def build_pipeline(requested_mode: str) -> tuple[AidFitAgentPipeline, str]:
    mode = resolve_mode(requested_mode)
    if mode == "gemini" and not settings.gemini_api_key.strip():
        raise LocalChatConfigurationError(
            "Gemini 모드에는 .env의 GEMINI_API_KEY가 필요합니다."
        )

    nodes = AgentNodes(
        vlm_service=VlmService(),
        rag_service=RagService(),
        llm_service=LlmService(),
    )
    with suppress_langchain_deprecation_warning():
        pipeline = AidFitAgentPipeline(nodes)
    return pipeline, mode


def format_agent_response(response: dict[str, Any]) -> str:
    lines = [str(response.get("message") or "응답 메시지가 없습니다.")]

    recommendations = response.get("recommendations")
    if isinstance(recommendations, list) and recommendations:
        lines.extend(["", "추천 상품"])
        for index, item in enumerate(recommendations, start=1):
            if not isinstance(item, dict):
                continue
            brand = str(item.get("brand") or "").strip()
            name = str(item.get("item_name") or item.get("name") or "이름 없음").strip()
            label = " ".join(part for part in (brand, name) if part)
            price = item.get("price")
            price_text = f" · {price:,.0f}원" if isinstance(price, (int, float)) else ""
            lines.append(f"  {index}. {label}{price_text}")
            reason = str(item.get("reason") or "").strip()
            if reason:
                lines.append(f"     이유: {reason}")
            product_url = str(item.get("product_url") or "").strip()
            if product_url:
                lines.append(f"     링크: {product_url}")

    style_guide = response.get("style_guide")
    if isinstance(style_guide, dict) and style_guide.get("summary"):
        lines.extend(["", f"스타일 가이드: {style_guide['summary']}"])
        tips = style_guide.get("tips")
        if isinstance(tips, list):
            lines.extend(f"  - {tip}" for tip in tips if tip)

    return "\n".join(lines)


def format_trace(trace: dict[str, Any]) -> str:
    trace_view = {
        "intent": trace.get("intent"),
        "intent_reason": trace.get("intent_reason"),
        "resolved_query": trace.get("resolved_query"),
        "retrieval_action": trace.get("retrieval_action"),
        "candidate_scope": trace.get("candidate_scope"),
        "retrieval_target": trace.get("retrieval_target"),
        "retrieval_reason": trace.get("retrieval_reason"),
        "rag_reused": trace.get("rag_reused", False),
        "rag_item_count": len(trace.get("rag_items") or []),
        "candidate_pool_count": len(trace.get("candidate_pool") or []),
        "shown_item_count": len(trace.get("shown_item_refs") or []),
        "error": trace.get("error"),
    }
    return json.dumps(trace_view, ensure_ascii=False, indent=2)


def configure_windows_console() -> None:
    # PowerShell 5 and redirected output can otherwise render Korean incorrectly.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (AttributeError, OSError):
                pass


def print_help() -> None:
    print(
        "명령어\n"
        "  /help          명령어 보기\n"
        "  /reset         대화 및 RAG 문맥 초기화\n"
        "  /trace         실행 경로 출력 켜기/끄기\n"
        "  /image <URL>   다음 질문에 이미지 URL 첨부\n"
        "  /images        대기 중인 이미지 URL 보기\n"
        "  /exit          종료"
    )


async def run_one_turn(
    session: LocalChatSession,
    query: str,
    *,
    image_urls: list[str],
    show_trace: bool,
) -> int:
    try:
        trace = await session.send(query, image_urls=image_urls)
    except Exception as exc:
        print(f"Agent 실행 오류: {exc}", file=sys.stderr)
        return 1

    response = trace["response"]
    print(f"\nAgent > {format_agent_response(response)}")
    if show_trace:
        print(f"\n[trace]\n{format_trace(trace)}")
    return 1 if response.get("status") == "error" else 0


async def run_interactive(
    session: LocalChatSession,
    *,
    mode: str,
    show_trace: bool,
) -> int:
    pending_image_urls: list[str] = []
    print(f"AID-FIT Agent 로컬 채팅 (mode: {mode})")
    print("FastAPI, DB, 프론트엔드 없이 현재 PowerShell 프로세스에서 실행 중입니다.")
    print_help()

    while True:
        try:
            raw_input = input("\n나 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n채팅을 종료합니다.")
            return 0

        if not raw_input:
            continue
        command = raw_input.lower()
        if command in {"/exit", "/quit", "exit", "quit"}:
            print("채팅을 종료합니다.")
            return 0
        if command == "/help":
            print_help()
            continue
        if command == "/reset":
            session.reset()
            pending_image_urls.clear()
            print("대화 및 RAG 문맥을 초기화했습니다.")
            continue
        if command == "/trace":
            show_trace = not show_trace
            print(f"trace 출력을 {'켰습니다' if show_trace else '껐습니다'}.")
            continue
        if command == "/images":
            if pending_image_urls:
                print("대기 중인 이미지:")
                print("\n".join(f"  - {url}" for url in pending_image_urls))
            else:
                print("대기 중인 이미지가 없습니다.")
            continue
        if command.startswith("/image "):
            image_url = raw_input[7:].strip()
            if image_url:
                pending_image_urls.append(image_url)
                print("다음 질문에 이미지를 첨부합니다.")
            else:
                print("사용법: /image <URL>")
            continue
        if command.startswith("/"):
            print("알 수 없는 명령어입니다. /help로 명령어를 확인하세요.")
            continue

        await run_one_turn(
            session,
            raw_input,
            image_urls=list(pending_image_urls),
            show_trace=show_trace,
        )
        pending_image_urls.clear()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the AID-FIT agent locally without FastAPI, DB, or frontend."
    )
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default="auto",
        help="gemini는 Gemini를 호출한다. auto도 같다 — 목업 경로는 없다.",
    )
    parser.add_argument("--query", help="Run one question and exit instead of opening interactive chat.")
    parser.add_argument(
        "--image-url",
        action="append",
        default=[],
        help="Attach an image URL. Repeat the option for multiple images.",
    )
    parser.add_argument("--trace", action="store_true", help="Print a concise agent routing trace.")
    parser.add_argument("--user-id", default="local-powershell", help="In-memory test user identifier.")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    try:
        pipeline, mode = build_pipeline(args.mode)
    except LocalChatConfigurationError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2

    session = LocalChatSession(pipeline, user_id=args.user_id)
    if args.query:
        return await run_one_turn(
            session,
            args.query,
            image_urls=args.image_url,
            show_trace=args.trace,
        )
    return await run_interactive(session, mode=mode, show_trace=args.trace)


def main() -> int:
    configure_windows_console()
    args = build_parser().parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\n채팅을 종료합니다.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
