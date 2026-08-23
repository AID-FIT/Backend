from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import chromadb
from chromadb import PersistentClient
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "preprocessed_vlm_dataset"
DB_PATH = BASE_DIR / "data" / "chromadb_final"
ERROR_PATH = BASE_DIR / "data" / "build_chromadb_errors.json"

COLLECTION_NAME = "musinsa"
MODEL_NAME = "jhgan/ko-sroberta-multitask"
BATCH_SIZE = 100

REQUIRED_FIELDS = (
    "item_id",
    "product_id",
    "name",
    "brand",
    "price",
    "category",
    "label",
    "gender",
    "thumbnail_url",
    "product_url",
    "item_type",
    "color",
    "material",
    "fit",
    "pattern",
    "mood",
    "sense_of_season",
    "season_norm",
    "search_document",
    "source_file",
)

METADATA_FIELDS = (
    "item_id",
    "product_id",
    "name",
    "brand",
    "price",
    "category",
    "label",
    "gender",
    "thumbnail_url",
    "product_url",
    "item_type",
    "color",
    "material",
    "fit",
    "pattern",
    "mood",
    "sense_of_season",
    "season_norm",
    "source_file",
)


def load_items() -> list[dict[str, Any]]:
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"Input directory not found: {DATA_DIR}")

    items: list[dict[str, Any]] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            raise ValueError(f"{path.name}: JSON root must be a list")
        items.extend(data)
        print(f"{path.name}: loaded={len(data)}")
    return items


def metadata_value(value: Any) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_metadata(item: dict[str, Any]) -> dict[str, str | int | float | bool]:
    return {field: metadata_value(item.get(field)) for field in METADATA_FIELDS}


def validate_item(item: dict[str, Any], index: int, seen_ids: set[str]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    item_id = item.get("item_id")

    for field in REQUIRED_FIELDS:
        if field not in item:
            errors.append({"index": index, "item_id": item_id, "error": "missing_field", "field": field})
        elif item[field] is None or (isinstance(item[field], str) and not item[field].strip()):
            errors.append({"index": index, "item_id": item_id, "error": "empty_field", "field": field})

    if item_id in seen_ids:
        errors.append({"index": index, "item_id": item_id, "error": "duplicate_item_id"})
    elif isinstance(item_id, str) and item_id.strip():
        seen_ids.add(item_id)

    return errors


def recreate_collection() -> chromadb.Collection:
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME,
        local_files_only=True,
    )
    client = PersistentClient(path=str(DB_PATH))

    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection: {COLLECTION_NAME}")
    except Exception:
        print(f"No existing collection to delete: {COLLECTION_NAME}")

    return client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"},
    )


def flush_batch(
    collection: chromadb.Collection,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict[str, str | int | float | bool]],
) -> int:
    if not ids:
        return 0
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


def run_smoke_tests(collection: chromadb.Collection) -> None:
    queries = (
        "검정색 오버핏 셔츠",
        "여름 반팔티",
        "봄 가을 와이드팬츠",
        "여자 미니원피스",
        "남자 스니커즈",
    )

    print("\nSmoke test")
    print("-" * 80)
    for query in queries:
        result = collection.query(
            query_texts=[query],
            n_results=3,
            include=["metadatas", "distances"],
        )
        metadatas = result.get("metadatas", [[]])[0] or []
        distances = result.get("distances", [[]])[0] or []
        print(f"\nquery: {query}")
        for rank, (metadata, distance) in enumerate(zip(metadatas, distances), start=1):
            print(
                f"{rank}. distance={float(distance):.4f} "
                f"name={metadata.get('name')} | category={metadata.get('category')} | "
                f"item_type={metadata.get('item_type')} | gender={metadata.get('gender')} | "
                f"price={metadata.get('price')} | url={metadata.get('product_url')}"
            )


def save_errors(errors: list[dict[str, Any]]) -> None:
    with ERROR_PATH.open("w", encoding="utf-8") as file:
        json.dump(errors, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> None:
    print(f"DATA_DIR={DATA_DIR}")
    print(f"DB_PATH={DB_PATH}")
    print(f"COLLECTION_NAME={COLLECTION_NAME}")
    print(f"MODEL_NAME={MODEL_NAME}")
    print("-" * 80)

    items = load_items()
    total_input = len(items)
    print("-" * 80)
    print(f"total_input={total_input}")

    seen_ids: set[str] = set()
    errors: list[dict[str, Any]] = []
    valid_records: list[tuple[str, str, dict[str, str | int | float | bool]]] = []

    for index, item in enumerate(items):
        item_errors = validate_item(item, index, seen_ids)
        if item_errors:
            errors.extend(item_errors)
            continue

        valid_records.append((
            str(item["item_id"]),
            str(item["search_document"]),
            build_metadata(item),
        ))

    save_errors(errors)
    print(f"validation_errors={len(errors)}")
    print(f"valid_records={len(valid_records)}")
    print(f"error_log={ERROR_PATH}")

    collection = recreate_collection()
    saved = 0
    batch_ids: list[str] = []
    batch_documents: list[str] = []
    batch_metadatas: list[dict[str, str | int | float | bool]] = []

    for item_id, document, metadata in valid_records:
        batch_ids.append(item_id)
        batch_documents.append(document)
        batch_metadatas.append(metadata)

        if len(batch_ids) >= BATCH_SIZE:
            saved += flush_batch(collection, batch_ids, batch_documents, batch_metadatas)
            batch_ids, batch_documents, batch_metadatas = [], [], []
            print(f"saved={saved}/{len(valid_records)}")

    saved += flush_batch(collection, batch_ids, batch_documents, batch_metadatas)

    count = collection.count()
    print("-" * 80)
    print(f"total_input={total_input}")
    print(f"saved={saved}")
    print(f"collection_count={count}")
    print(f"build_errors={len(errors)}")

    if errors:
        print("Build finished with validation errors. See build_chromadb_errors.json")
    if count != len(valid_records):
        raise RuntimeError(f"Collection count mismatch: count={count}, valid_records={len(valid_records)}")
    if total_input != saved:
        raise RuntimeError(f"Saved count mismatch: total_input={total_input}, saved={saved}")

    run_smoke_tests(collection)


if __name__ == "__main__":
    main()


