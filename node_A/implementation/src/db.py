import aiosqlite
from typing import Iterable
from .config import DB_PATH


async def get_document_texts(doc_ids: list[str]) -> str:
    """Fetch `text` for each doc_id in order and return concatenated string.

    Missing IDs are ignored. Returned pieces are separated by two newlines.
    """
    if not doc_ids:
        return ""

    parts: list[str] = []
    # Connect asynchronously to the SQLite DB
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure we get strings back (row[0] is `text` column)
        for doc_id in doc_ids:
            async with db.execute("SELECT text FROM passages WHERE doc_id = ?", (doc_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    parts.append(row[0])

    return "\n\n".join(parts)
