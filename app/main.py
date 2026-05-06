from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings


def _is_allowed_origin(origin: str) -> bool:
    return "*" in settings.cors_origins or origin in settings.cors_origins


def _preflight_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    request_headers = request.headers.get("access-control-request-headers", "*")
    headers = {
        "Access-Control-Allow-Methods": "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT",
        "Access-Control-Allow-Headers": request_headers,
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }

    if origin and _is_allowed_origin(origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    if request.headers.get("access-control-request-private-network") == "true":
        headers["Access-Control-Allow-Private-Network"] = "true"

    return headers


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.middleware("http")
    async def preflight_middleware(request: Request, call_next) -> Response:
        if (
            request.method == "OPTIONS"
            and request.headers.get("origin")
            and request.headers.get("access-control-request-method")
        ):
            return Response(status_code=204, headers=_preflight_headers(request))

        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
