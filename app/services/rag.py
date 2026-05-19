"""RAGService — HyDE -> hybrid (FTS + dense weighted RRF) -> rerank -> parent-doc -> top-5.

Interface:
    retrieve(query, filters: RetrievalFilters | None) -> list[RetrievedChunk]

Post-filter w/ over-fetch (top-200 per side, fuse to 20, rerank to 5).
"""
