"""
DevMesh Platform - Template Cache

In-memory dict mapping template_hash -> template_id.
Avoids DB lookups for known templates during ingest.

Each uvicorn worker has its own cache. Cache miss hits DB,
finds the template, and caches it — one-time cost per worker
per new template.
"""

import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 100_000


class TemplateCache:
    """LRU-ish in-memory cache for template_hash -> template_id."""

    def __init__(self, max_size: int = MAX_CACHE_SIZE):
        self._cache: OrderedDict[str, int] = OrderedDict()
        self._max_size = max_size

    def get(self, template_hash: str) -> int | None:
        """Look up template_id by hash. Returns None on miss."""
        tid = self._cache.get(template_hash)
        if tid is not None:
            # Move to end (most recently used)
            self._cache.move_to_end(template_hash)
        return tid

    def put(self, template_hash: str, template_id: int) -> None:
        """Insert or update a cache entry."""
        if template_hash in self._cache:
            self._cache.move_to_end(template_hash)
            self._cache[template_hash] = template_id
        else:
            if len(self._cache) >= self._max_size:
                # Evict oldest entry
                self._cache.popitem(last=False)
            self._cache[template_hash] = template_id

    def warm(self, rows: list[dict]) -> None:
        """Bulk load from DB rows (each row has 'template_hash' and 'id').

        Args:
            rows: List of dicts with 'template_hash' and 'id' keys.
        """
        for row in rows:
            self.put(row['template_hash'], row['id'])
        logger.info("Template cache warmed with %d entries", len(rows))

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()
