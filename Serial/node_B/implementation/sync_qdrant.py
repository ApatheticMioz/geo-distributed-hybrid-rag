"""Copy a Qdrant collection from Node A into Node B.

This is intended for migrating the expensive 1M-point test index from the
Node A Qdrant instance into the Node B Qdrant instance without re-embedding.
"""

import argparse
import os
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from qdrant_client.models import Distance, PointStruct, VectorParams


def _vector_params_from_source(source_info: Any) -> VectorParams:
    vectors = source_info.config.params.vectors
    if isinstance(vectors, dict):
        vector_params = next(iter(vectors.values()))
        return VectorParams(size=vector_params.size, distance=vector_params.distance)
    return VectorParams(size=vectors.size, distance=vectors.distance)


def sync_collection(
    source_host: str,
    target_host: str,
    collection_name: str,
    source_port: int,
    target_port: int,
    batch_size: int,
    grpc_port: int,
) -> None:
    source_client = QdrantClient(host=source_host, port=source_port, grpc_port=grpc_port, prefer_grpc=True)
    target_client = QdrantClient(host=target_host, port=target_port, grpc_port=grpc_port, prefer_grpc=True)

    source_info = source_client.get_collection(collection_name)
    vector_params = _vector_params_from_source(source_info)

    try:
        target_client.get_collection(collection_name)
    except Exception:
        target_client.create_collection(
            collection_name=collection_name,
            vectors_config=vector_params,
        )

    offset = None
    total_copied = 0

    while True:
        points, offset = source_client.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )

        if not points:
            break

        target_points = []
        for point in points:
            target_points.append(
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload or {},
                )
            )

        target_client.upsert(collection_name=collection_name, points=target_points, wait=True)
        total_copied += len(target_points)
        print(f"Copied {total_copied:,} points...")

        if offset is None:
            break

    target_info = target_client.get_collection(collection_name)
    print(f"Done. Source points: {source_info.points_count}, target points: {target_info.points_count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-host", default=os.environ.get("SOURCE_QDRANT_HOST", "10.8.0.1"))
    parser.add_argument("--source-port", type=int, default=int(os.environ.get("SOURCE_QDRANT_PORT", "6333")))
    parser.add_argument("--target-host", default=os.environ.get("TARGET_QDRANT_HOST", "10.8.0.2"))
    parser.add_argument("--target-port", type=int, default=int(os.environ.get("TARGET_QDRANT_PORT", "6333")))
    parser.add_argument("--grpc-port", type=int, default=int(os.environ.get("QDRANT_GRPC_PORT", "6334")))
    parser.add_argument("--collection", default=os.environ.get("QDRANT_COLLECTION", "msmarco_passages"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("QDRANT_SYNC_BATCH_SIZE", "256")))
    args = parser.parse_args()

    sync_collection(
        source_host=args.source_host,
        target_host=args.target_host,
        collection_name=args.collection,
        source_port=args.source_port,
        target_port=args.target_port,
        batch_size=args.batch_size,
        grpc_port=args.grpc_port,
    )


if __name__ == "__main__":
    main()