from app.db import SessionLocal
from app.rag.ingest import split_markdown
from app.rag.retriever import hybrid_search


def test_split_markdown_by_section():
    chunks = split_markdown("doc", "# Title\n\n## First\nAlpha text.\n\n## Second\nBeta text.")
    assert [c.section for c in chunks] == ["First", "Second"]
    assert chunks[0].content.startswith("Title - First")


def test_long_sections_are_split_with_overlap():
    body = " ".join(f"Sentence number {i}." for i in range(400))
    chunks = split_markdown("doc", f"# T\n\n## Long\n{body}")
    assert len(chunks) > 1
    assert all(len(c.content) < 1400 for c in chunks)


async def test_hybrid_search_finds_the_right_article():
    async with SessionLocal() as session:
        results = await hybrid_search(session, "What is the maximum order value for cash on delivery?")
    assert results[0].doc_id == "payments"
    assert results[0].similarity > 0
