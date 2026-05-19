"""MemoryService — episodic long-term memory.

Interface:
    write(user_id, summary, entities) -> memory_id   (also writes audit row in same tx)
    recall(user_id, query, top_k=5, min_similarity=0.6) -> list[Memory]

Embedding via bge-base. Redaction applied before persistence.
"""
