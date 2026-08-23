import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "scripts"))

from run_vlm_only import resolve_inputs  # noqa: E402

from app.agent.agent_pipeline import AidFitAgentPipeline  # noqa: E402


def print_trace(trace: dict) -> None:
    vlm_items = trace.get("vlm_items") or []
    print(f"의도       : {trace.get('intent')}")
    print(f"검색 대상  : {trace.get('retrieval_target')}")
    print(f"VLM 아이템 : {len(vlm_items)}개")
    for item in vlm_items:
        print(
            f"   - [{item.get('category')}] {item.get('name')} | "
            f"{item.get('color')} / {item.get('material')} / {item.get('fit')} / {item.get('sense_of_season')}"
        )
    print(f"RAG 결과   : {len(trace.get('rag_items') or [])}건")
    if trace.get("error"):
        print(f"에러       : {trace['error']}")
    print("-" * 60)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the whole recommendation agent once, end to end.")
    parser.add_argument("query", help="User request, e.g. '이 코디에 어울리는 가방 추천해줘'")
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        dest="images",
        help="Image URL or local file path. Repeat the flag for several images.",
    )
    parser.add_argument("--user-id", default="local-test-user")
    parser.add_argument("--age-group", default="20s")
    parser.add_argument(
        "--style",
        action="append",
        default=[],
        dest="styles",
        help="Preferred style, e.g. --style minimal --style casual",
    )
    parser.add_argument(
        "--no-closet-style",
        action="store_true",
        help="Ignore the existing closet style when ranking.",
    )
    parser.add_argument("--mock", action="store_true", help="Force mock AI services.")
    parser.add_argument("--json", action="store_true", help="Print the raw response JSON only.")
    args = parser.parse_args()

    if args.mock:
        # Mock mode is read from settings at service construction time.
        from app.core import config

        config.settings.use_mock_ai = True

    image_urls, server = resolve_inputs(list(args.images))
    if server is not None:
        for original, url in zip(args.images, image_urls):
            if original != url:
                print(f"[local] {original} -> {url}", file=sys.stderr)

    try:
        trace = await AidFitAgentPipeline().run(
            query=args.query,
            user_id=args.user_id,
            image_urls=image_urls,
            use_closet_style=not args.no_closet_style,
            user_profile={"age_group": args.age_group, "preferred_styles": args.styles},
            return_trace=True,
        )
    finally:
        if server is not None:
            server.close()

    if not args.json:
        print_trace(trace)
    print(json.dumps(trace["response"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
