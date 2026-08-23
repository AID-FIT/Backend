"""ChromaDB의 상품 메타데이터를 Gemini로 재임베딩해 pgvector로 옮긴다.

기존 벡터(ko-sroberta, 768차원)는 그대로 쓸 수 없다. 질의 시점에도 같은 모델이
필요한데 sentence-transformers는 PyTorch를 끌고 와 서버리스에 올라가지 않는다.
그래서 메타데이터만 가져와 HTTP로 부를 수 있는 임베딩으로 다시 만든다.

chromadb 패키지 없이 sqlite를 직접 읽으므로 무거운 의존성이 필요 없다.

    DATABASE_URL=... GEMINI_API_KEY=... python scripts/migrate_chroma_to_pgvector.py [--limit N] [--dry-run]
"""
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.embedding_service import EmbeddingService  # noqa: E402

TABLE = "product_vectors"
COLLECTION = "musinsa"
EMBED_CHUNK = 100
INSERT_CHUNK = 200

# ChromaDB 메타데이터 키 → 우리 컬럼.
FIELD_MAP = {
    "name": "name",
    "brand": "brand",
    "category": "category",
    "label": "label",
    "gender": "gender",
    "color": "color",
    "material": "material",
    "fit": "fit",
    "pattern": "pattern",
    "product_url": "product_url",
}


def read_chroma_rows(db_path: Path, collection: str, limit: int | None) -> list[dict]:
    """chroma.sqlite3에서 문서와 메타데이터를 읽는다."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    segment = cur.execute(
        "SELECT id FROM segments WHERE collection = (SELECT id FROM collections WHERE name = ?)"
        " AND scope = 'METADATA'",
        (collection,),
    ).fetchone()
    if segment is None:
        con.close()
        raise SystemExit(f"collection not found: {collection}")

    # 메타데이터는 키-값 행으로 쪼개져 있어 embedding_id 기준으로 다시 모은다.
    query = """
        SELECT e.embedding_id AS embedding_id,
               m.key          AS key,
               COALESCE(m.string_value, CAST(m.int_value AS TEXT), CAST(m.float_value AS TEXT)) AS value
        FROM embeddings e
        JOIN embedding_metadata m ON m.id = e.id
        WHERE e.segment_id = ?
        ORDER BY e.id
    """
    grouped: dict[str, dict] = {}
    for row in cur.execute(query, (segment["id"],)):
        entry = grouped.setdefault(row["embedding_id"], {})
        entry[row["key"]] = row["value"]
    con.close()

    rows = list(grouped.values())
    return rows[:limit] if limit else rows


def build_document(meta: dict) -> str:
    """임베딩할 텍스트. Chroma가 쓰던 문서가 있으면 그대로 쓴다."""
    document = (meta.get("chroma:document") or "").strip()
    if document:
        return document

    parts = [
        meta.get("name"),
        meta.get("brand"),
        meta.get("category"),
        meta.get("label"),
        meta.get("color"),
        meta.get("material"),
        meta.get("fit"),
        meta.get("pattern"),
        meta.get("mood"),
        meta.get("sense_of_season"),
    ]
    return " ".join(str(part) for part in parts if part)


def to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_record(meta: dict) -> dict | None:
    item_id = (meta.get("item_id") or meta.get("product_id") or "").strip()
    document = build_document(meta)
    if not item_id or not document:
        return None

    record = {key: meta.get(source) for source, key in FIELD_MAP.items()}
    record.update(
        {
            "item_id": item_id,
            "source": "musinsa",
            "mood": meta.get("mood"),
            "season": meta.get("season_norm") or meta.get("sense_of_season"),
            "price": to_int(meta.get("price")),
            "image_url": meta.get("thumbnail_url"),
            "document": document,
        }
    )
    return record


async def upsert(engine, records: list[dict]) -> None:
    statement = text(
        f"""
        INSERT INTO {TABLE} (
            item_id, source, name, brand, category, label, gender, color, material,
            fit, pattern, mood, season, price, image_url, product_url, document, embedding
        ) VALUES (
            :item_id, :source, :name, :brand, :category, :label, :gender, :color, :material,
            :fit, :pattern, :mood, :season, :price, :image_url, :product_url, :document,
            CAST(:embedding AS vector)
        )
        ON CONFLICT (item_id) DO UPDATE SET
            source = EXCLUDED.source, name = EXCLUDED.name, brand = EXCLUDED.brand,
            category = EXCLUDED.category, label = EXCLUDED.label, gender = EXCLUDED.gender,
            color = EXCLUDED.color, material = EXCLUDED.material, fit = EXCLUDED.fit,
            pattern = EXCLUDED.pattern, mood = EXCLUDED.mood, season = EXCLUDED.season,
            price = EXCLUDED.price, image_url = EXCLUDED.image_url,
            product_url = EXCLUDED.product_url, document = EXCLUDED.document,
            embedding = EXCLUDED.embedding, updated_at = now()
        """
    )
    async with engine.begin() as conn:
        await conn.execute(statement, records)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="옮길 최대 건수")
    parser.add_argument("--dry-run", action="store_true", help="임베딩과 적재 없이 읽기만 한다")
    parser.add_argument("--force", action="store_true", help="이미 적재된 건도 다시 임베딩한다")
    args = parser.parse_args()

    db_path = Path(settings.rag_vector_db_path) / "chroma.sqlite3"
    if not db_path.exists():
        raise SystemExit(f"chroma sqlite not found: {db_path}")

    raw_rows = read_chroma_rows(db_path, COLLECTION, args.limit)
    records = [record for record in (build_record(meta) for meta in raw_rows) if record]
    skipped = len(raw_rows) - len(records)
    print(f"읽음 {len(raw_rows)}건 · 유효 {len(records)}건 · 건너뜀 {skipped}건")

    if args.dry_run:
        for record in records[:3]:
            print(f"  {record['item_id']}  {record['category']}  {record['document'][:60]}")
        return

    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )

    # 중간에 끊겨도 다시 돌리면 남은 것부터 이어간다. 12,794건을 처음부터
    # 다시 임베딩하면 시간과 비용이 그대로 또 든다.
    if not args.force:
        async with engine.connect() as conn:
            existing = await conn.execute(text(f"SELECT item_id FROM {TABLE}"))
            already = {row[0] for row in existing}
        before = len(records)
        records = [record for record in records if record["item_id"] not in already]
        if before != len(records):
            print(f"이미 적재된 {before - len(records)}건 건너뜀 · 남은 {len(records)}건")

    if not records:
        print("옮길 것이 없습니다.")
        await engine.dispose()
        return

    embedding_service = EmbeddingService()
    done = 0

    for start in range(0, len(records), INSERT_CHUNK):
        batch = records[start : start + INSERT_CHUNK]
        vectors = await embedding_service.embed_documents_chunked(
            [record["document"] for record in batch],
            chunk_size=EMBED_CHUNK,
        )
        for record, vector in zip(batch, vectors, strict=True):
            record["embedding"] = str(vector)

        await upsert(engine, batch)
        done += len(batch)
        print(f"  적재 {done}/{len(records)}")

    async with engine.connect() as conn:
        total = await conn.execute(text(f"SELECT count(*) FROM {TABLE}"))
        print(f"\n{TABLE}: {total.scalar_one()}건")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
