"""로그인 경로에 열려 있는 문이 없는지 확인한다.

`POST /auth/login`은 비밀번호를 검증하지 않고 입력한 이메일로 토큰을
발급하고 있었다. 아무 이메일이나 보내면 그 계정의 토큰이 나왔다. 게다가
그 토큰은 sub에 이메일이 들어가는데 `get_current_user`는 그 값으로
User.id(UUID)를 조회하므로, 인증을 통과해도 401이 아니라 500이 났다.

비밀번호 저장소도 없다 — password_hash는 소셜 로그인에서 None으로만
저장되고, 해시 라이브러리도 의존성에 없다. 그래서 구현이 아니라 제거했다.
"""

from app.main import app


def routes() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_password_login_is_gone() -> None:
    assert not any(path.endswith("/auth/login") for path in routes())


def test_social_login_is_the_only_way_in() -> None:
    login_paths = sorted(p for p in routes() if "/auth/" in p)

    assert login_paths == ["/api/v1/auth/apple", "/api/v1/auth/google"]


def test_no_schema_is_left_behind_for_password_login() -> None:
    # 스키마만 남겨 두면 다음 사람이 되살리기 쉽다.
    import app.schemas.auth as auth_schemas

    assert not hasattr(auth_schemas, "LoginRequest")
