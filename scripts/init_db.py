import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import Base
from app.db.session import engine


async def apply_lightweight_migrations(connection) -> None:
    await connection.exec_driver_sql("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(30) DEFAULT 'guest'")
    await connection.exec_driver_sql("UPDATE users SET role = 'user' WHERE role IS NULL")

    # create_all은 이미 있는 테이블을 바꾸지 않는다. 컬럼도 CHECK 제약도 여기서만 붙는다.
    # 배포에 마이그레이션 단계가 없으므로(Vercel), 이 스크립트를 배포 전에 직접 돌려야 한다.
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS gender VARCHAR(20)"
    )
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS height_cm SMALLINT"
    )
    # DROP 후 ADD라야 몇 번을 돌려도 같은 상태가 된다. ADD CONSTRAINT에는 IF NOT EXISTS가 없다.
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS ck_user_preferences_gender"
    )
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences ADD CONSTRAINT ck_user_preferences_gender "
        "CHECK (gender IS NULL OR gender IN ('men', 'women', 'unisex'))"
    )
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences DROP CONSTRAINT IF EXISTS ck_user_preferences_height_cm"
    )
    await connection.exec_driver_sql(
        "ALTER TABLE user_preferences ADD CONSTRAINT ck_user_preferences_height_cm "
        "CHECK (height_cm IS NULL OR (height_cm BETWEEN 100 AND 250))"
    )


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await apply_lightweight_migrations(connection)


if __name__ == "__main__":
    asyncio.run(main())
