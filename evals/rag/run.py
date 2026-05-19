"""RAG eval.

Retrieval (deterministic): hit@5, MRR@10 via set intersection on chunk_ids.
Generation (RAGAS w/ Groq judge): faithfulness, answer_relevancy.
Plus hand-label agreement (Spearman) on 5/25.
"""
