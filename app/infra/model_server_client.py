"""HTTP client to model-server. Maps failures -> ToolFailure subclasses.

Interface:
    classify(text), extract(text), summarize(text),
    rerank(query, candidates), embed(texts, mode='query'|'passage')
"""
