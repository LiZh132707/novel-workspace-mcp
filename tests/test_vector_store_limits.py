from vector_store import VectorStore


def test_search_clamps_invalid_top_k_and_keeps_cross_novel_hits_distinct():
    store = VectorStore.__new__(VectorStore)
    store._semantic_disabled = True
    store._lexical_search = lambda _query, novel, _top_k: [
        {"novel": "a", "chapter": 1, "chunk_index": 0, "score": 0.9},
        {"novel": "b", "chapter": 1, "chunk_index": 0, "score": 0.8},
    ]
    result = store.search("term", novel=None, top_k=5)
    assert {item["novel"] for item in result} == {"a", "b"}
    assert len(store.search("term", novel=None, top_k=-10)) == 1
