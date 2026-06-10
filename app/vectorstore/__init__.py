"""Vector store package.

The live store is pgvector (``PgVectorStore``). The legacy ``chroma_db`` module
is no longer imported here so that ``chromadb`` is not a runtime dependency — it
is kept on disk for reference only. Import it explicitly if you ever need it.
"""
