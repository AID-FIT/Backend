import argparse
import asyncio
import json
import mimetypes
import shutil
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.schemas.ai import VLMResponse
from app.services.vlm_service import VlmService


SAMPLE_IMAGE_URLS = [
    "https://image.msscdn.net/images/goods_img/20260330/6217185/6217185_17748366573898_500.jpg",
]


def is_remote_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return None


class LocalImageServer:
    # Serving local photos over http keeps the real download path under test.
    def __init__(self, paths: list[Path]) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="aidfit-vlm-"))
        names: list[str] = []
        for index, path in enumerate(paths):
            mime_type, _ = mimetypes.guess_type(path.name)
            if not (mime_type or "").startswith("image/"):
                shutil.rmtree(self.directory, ignore_errors=True)
                raise ValueError(f"이미지 확장자가 아닙니다: {path}")
            served_name = f"{index}_{path.name}"
            shutil.copyfile(path, self.directory / served_name)
            names.append(served_name)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(self.directory)))
        port = self.server.server_address[1]
        self.urls = [f"http://127.0.0.1:{port}/{quote(name)}" for name in names]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.directory, ignore_errors=True)


def resolve_inputs(values: list[str]) -> tuple[list[str], LocalImageServer | None]:
    local_indexes = [index for index, value in enumerate(values) if not is_remote_url(value)]
    if not local_indexes:
        return list(values), None

    paths = []
    for index in local_indexes:
        path = Path(values[index]).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
        paths.append(path)

    server = LocalImageServer(paths)
    resolved = list(values)
    for index, url in zip(local_indexes, server.urls):
        resolved[index] = url
    return resolved, server


async def analyze_images(service, image_urls: list[str], closet_mode: bool) -> dict:
    # Mirror the two production call sites so a manual run tests the real thing.
    if not closet_mode:
        return await service.analyze_many(image_urls)

    items = [await service.analyze(image_url) for image_url in image_urls]
    return {
        "items": items,
        "is_fashion_item": all(item["is_fashion_item"] for item in items) if items else True,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run only the VLM analysis step on image URLs or local photos.")
    parser.add_argument(
        "images",
        nargs="*",
        default=SAMPLE_IMAGE_URLS,
        help="Public image URLs or local image file paths. Defaults to one sample product image.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock VLM path instead of calling Gemini.",
    )
    parser.add_argument(
        "--closet",
        action="store_true",
        help="Use the closet upload path (one item per photo) instead of the recommendation path.",
    )
    args = parser.parse_args()

    image_urls, server = resolve_inputs(list(args.images))
    if server is not None:
        for original, url in zip(args.images, image_urls):
            if original != url:
                print(f"[local] {original} -> {url}", file=sys.stderr)

    try:
        service = VlmService(use_mock_ai=args.mock)
        response = await analyze_images(service, image_urls, args.closet)
    finally:
        if server is not None:
            server.close()

    # Re-validate here so a manual run fails the same way the agent would.
    VLMResponse.model_validate(response)
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
