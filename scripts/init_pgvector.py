"""pgvector 확장과 상품 벡터 테이블을 준비한다.

DATABASE_URL 환경변수를 읽는다. 여러 번 실행해도 안전하다.

    DATABASE_URL=... python scripts/init_pgvector.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402

TABLE = "product_vectors"

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        item_id       TEXT PRIMARY KEY,
        source        TEXT NOT NULL DEFAULT 'musinsa',
        name          TEXT,
        brand         TEXT,
        category      TEXT,
        label         TEXT,
        gender        TEXT,
        color         TEXT,
        material      TEXT,
        fit           TEXT,
        pattern       TEXT,
        mood          TEXT,
        season        TEXT,
        price         INTEGER,
        image_url     TEXT,
        product_url   TEXT,
        document      TEXT NOT NULL,
        embedding     vector({{dims}}) NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # 메타데이터 선필터가 자주 걸리는 컬럼.
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_category ON {TABLE} (category)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_gender ON {TABLE} (gender)",
    f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_price ON {TABLE} (price)",
    # 코사인 거리 기준 근사 최근접 인덱스.
    f"""
    CREATE INDEX IF NOT EXISTS ix_{TABLE}_embedding
    ON {TABLE} USING hnsw (embedding vector_cosine_ops)
    """,
]


async def main() -> None:
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )

    async with engine.begin() as conn:
        for statement in STATEMENTS:
            sql = statement.format(dims=settings.embedding_dimensions)
            await conn.execute(text(sql))
            print(f"ok: {sql.strip().splitlines()[0][:70]}")

    async with engine.connect() as conn:
        count = await conn.execute(text(f"SELECT count(*) FROM {TABLE}"))
        print(f"\n{TABLE}: {count.scalar_one()} rows, dim={settings.embedding_dimensions}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
