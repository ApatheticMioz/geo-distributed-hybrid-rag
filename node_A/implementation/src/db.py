import logging
from pathlib import Path

import aiosqlite
from .config import DB_PATH


logger = logging.getLogger(__name__)


async def get_document_texts(doc_ids: list[str]) -> str:
    """Fetch `text` for each doc_id in order and return concatenated string.

    Missing IDs are ignored. Returned pieces are separated by two newlines.
    """
    if not doc_ids:
        return ""

    normalized_doc_ids = [str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()]
    if not normalized_doc_ids:
        return ""

    db_path = Path(DB_PATH).resolve()
    placeholders = ", ".join("?" for _ in normalized_doc_ids)
    query = f"SELECT doc_id, text FROM passages WHERE doc_id IN ({placeholders})"

    logger.debug("Hydrating docs from %s with query=%s vars=%s", db_path, query, normalized_doc_ids)

    parts_by_id: dict[str, str] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, normalized_doc_ids) as cursor:
            rows = await cursor.fetchall()
            for doc_id, text in rows:
                if text:
                    parts_by_id[str(doc_id)] = text

    missing_doc_ids = [doc_id for doc_id in normalized_doc_ids if doc_id not in parts_by_id]
    if missing_doc_ids:
        logger.debug("No SQLite rows found for doc_ids=%s in %s", missing_doc_ids, db_path)

    parts = [parts_by_id[doc_id] for doc_id in normalized_doc_ids if doc_id in parts_by_id]
    return "\n\n".join(parts)
