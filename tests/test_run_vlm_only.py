import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_vlm_only.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_vlm_only", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_vlm_only"] = module
    spec.loader.exec_module(module)
    return module


script = load_script()


def write_image(tmp_path: Path, name: str = "item.jpg", body: bytes = b"fake-jpeg-bytes") -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_remote_urls_are_passed_through_untouched() -> None:
    values = ["https://cdn.aidfit.com/item_001.jpg", "http://localhost:8000/uploads/a.jpg"]

    resolved, server = script.resolve_inputs(values)

    assert resolved == values
    assert server is None


def test_local_photo_is_served_over_http(tmp_path: Path) -> None:
    # The service only downloads http(s), so local files need a real URL.
    image = write_image(tmp_path)

    resolved, server = script.resolve_inputs([str(image)])
    try:
        assert server is not None
        assert resolved[0].startswith("http://127.0.0.1:")

        response = httpx.get(resolved[0], timeout=5)
        assert response.status_code == 200
        assert response.content == b"fake-jpeg-bytes"
        # content-type must be an image or the VLM service rejects the download.
        assert response.headers["content-type"].startswith("image/")
    finally:
        if server is not None:
            server.close()


def test_local_and_remote_inputs_keep_their_order(tmp_path: Path) -> None:
    first = write_image(tmp_path, "first.png", b"png-bytes")
    remote = "https://cdn.aidfit.com/item_002.jpg"
    second = write_image(tmp_path, "second.jpg", b"jpg-bytes")

    resolved, server = script.resolve_inputs([str(first), remote, str(second)])
    try:
        assert resolved[1] == remote
        assert httpx.get(resolved[0], timeout=5).content == b"png-bytes"
        assert httpx.get(resolved[2], timeout=5).content == b"jpg-bytes"
    finally:
        if server is not None:
            server.close()


def test_missing_local_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="파일을 찾을 수 없습니다"):
        script.resolve_inputs([str(tmp_path / "nope.jpg")])


def test_non_image_extension_is_rejected(tmp_path: Path) -> None:
    document = tmp_path / "notes.txt"
    document.write_text("not an image", encoding="utf-8")

    with pytest.raises(ValueError, match="이미지 확장자가 아닙니다"):
        script.resolve_inputs([str(document)])


def test_server_stops_and_cleans_up_after_close(tmp_path: Path) -> None:
    image = write_image(tmp_path)

    resolved, server = script.resolve_inputs([str(image)])
    directory = server.directory
    server.close()

    assert not directory.exists()
    with pytest.raises(httpx.HTTPError):
        httpx.get(resolved[0], timeout=2)


def test_real_download_path_accepts_the_served_photo(tmp_path: Path) -> None:
    # Runs the actual httpx download against a real server, not a fake client.
    import asyncio

    from app.services.vlm_service import VlmService

    image = write_image(tmp_path, "coat.jpg", b"binary-image-payload")
    resolved, server = script.resolve_inputs([str(image)])

    async def download() -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=5) as client:
            return await VlmService()._download_image(client, resolved[0])

    try:
        content, mime_type = asyncio.run(download())
    finally:
        server.close()

    assert content == b"binary-image-payload"
    assert mime_type == "image/jpeg"


class RedirectingServer:
    # Real server that answers /redirect.jpg with a 302 to the actual file.
    def __init__(self, body: bytes = b"redirected-image-bytes") -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return None

            def do_GET(self) -> None:
                outer.seen_user_agents.append(self.headers.get("User-Agent"))
                if self.path == "/redirect.jpg":
                    self.send_response(302)
                    self.send_header("Location", "/real.jpg")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

        self.body = body
        self.seen_user_agents: list = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/redirect.jpg"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def test_redirecting_image_url_is_followed(monkeypatch) -> None:
    # Image CDNs and presigned URLs redirect; a 302 must not read as an empty body.
    import asyncio

    from app.services import vlm_service as vlm_module
    from app.services.vlm_service import VlmService

    monkeypatch.setattr(vlm_module.settings, "vlm_max_image_bytes", 1024)
    server = RedirectingServer()

    async def download() -> tuple[bytes, str]:
        async with httpx.AsyncClient(
            timeout=5,
            follow_redirects=True,
            headers={"User-Agent": vlm_module.IMAGE_USER_AGENT},
        ) as client:
            return await VlmService()._download_image(client, server.url)

    try:
        content, mime_type = asyncio.run(download())
    finally:
        server.close()

    assert content == b"redirected-image-bytes"
    assert mime_type == "image/jpeg"
    assert all(agent == vlm_module.IMAGE_USER_AGENT for agent in server.seen_user_agents)


class RecordingVlmService:
    # Records which production call site the script chose.
    def __init__(self) -> None:
        self.many_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def analyze_many(self, image_urls: list[str]) -> dict:
        self.many_calls.append(image_urls)
        return {
            "items": [{"thumbnail_url": url, "is_fashion_item": True} for url in image_urls] * 2,
            "is_fashion_item": True,
        }

    async def analyze(self, image_url: str) -> dict:
        self.single_calls.append(image_url)
        return {"thumbnail_url": image_url, "is_fashion_item": True}


def test_default_run_uses_the_recommendation_path() -> None:
    import asyncio

    service = RecordingVlmService()
    urls = ["https://cdn.aidfit.com/a.jpg", "https://cdn.aidfit.com/b.jpg"]

    response = asyncio.run(script.analyze_images(service, urls, closet_mode=False))

    assert service.many_calls == [urls]
    assert service.single_calls == []
    assert len(response["items"]) == 4


def test_closet_flag_uses_the_single_item_path() -> None:
    import asyncio

    service = RecordingVlmService()
    urls = ["https://cdn.aidfit.com/a.jpg", "https://cdn.aidfit.com/b.jpg"]

    response = asyncio.run(script.analyze_images(service, urls, closet_mode=True))

    assert service.single_calls == urls
    assert service.many_calls == []
    assert len(response["items"]) == 2
    assert response["is_fashion_item"] is True


def test_closet_mode_marks_the_run_invalid_when_a_photo_is_not_a_garment() -> None:
    import asyncio

    class RejectingService(RecordingVlmService):
        async def analyze(self, image_url: str) -> dict:
            self.single_calls.append(image_url)
            return {"thumbnail_url": image_url, "is_fashion_item": "food" not in image_url}

    response = asyncio.run(
        script.analyze_images(
            RejectingService(),
            ["https://cdn.aidfit.com/a.jpg", "https://cdn.aidfit.com/food.jpg"],
            closet_mode=True,
        )
    )

    assert response["is_fashion_item"] is False


def test_empty_input_stays_valid_in_both_modes() -> None:
    import asyncio

    for closet_mode in (False, True):
        response = asyncio.run(script.analyze_images(RecordingVlmService(), [], closet_mode=closet_mode))
        assert response["is_fashion_item"] is True
