"""RAG 평가용 테스트 유저를 만들고(또는 재사용) 액세스 토큰을 출력한다.

DATABASE_URL / JWT_SECRET_KEY 는 대상 환경(로컬 또는 Supabase)과 같아야 한다.
토큰은 create_access_token 과 동일하게 발급되므로 배포 API에 그대로 쓸 수 있다.

    python scripts/seed_eval_user.py
    python scripts/seed_eval_user.py --email eval@aid-fit.test --nickname eval
"""

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from app.core.security import create_access_token
from app.db.models import User
from app.db.session import AsyncSessionLocal


async def seed(email: str, nickname: str) -> tuple[str, str]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(email=email, password_hash=None, nickname=nickname, role="user")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user.id, create_access_token(user.id)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="eval@aid-fit.test")
    parser.add_argument("--nickname", default="rag-eval")
    args = parser.parse_args()

    user_id, token = await seed(args.email, args.nickname)
    print(f"user_id: {user_id}")
    print(f"access_token: {token}")
    print()
    print(f'export AIDFIT_TOKEN="{token}"')


if __name__ == "__main__":
    asyncio.run(main())
