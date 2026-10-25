"""Ingestion pipeline: Markdown help articles -> chunks -> embeddings -> pgvector.

Chunks follow the article's headings (so each chunk is one topic), with a
size cap and overlap for long sections. A content hash makes re-indexing
idempotent: unchanged chunks are not embedded again, which saves cost.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import gateway
from app.logging_setup import log
from app.models import KnowledgeChunk

MAX_CHARS = 1200
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    doc_id: str
    doc_title: str
    section: str
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.doc_id}|{self.section}|{self.content}".encode()).hexdigest()


def split_markdown(doc_id: str, markdown: str) -> list[Chunk]:
    title_match = re.search(r"^#\s+(.+)$", markdown, re.M)
    title = title_match.group(1).strip() if title_match else doc_id
    sections = re.split(r"^##\s+", markdown, flags=re.M)
    chunks: list[Chunk] = []
    for raw in sections[1:] if len(sections) > 1 else sections:
        heading, _, body = raw.partition("\n")
        body = body.strip()
        if not body:
            continue
        section = heading.strip() if len(sections) > 1 else title
        start = 0
        while start < len(body):
            piece = body[start : start + MAX_CHARS]
            if start + MAX_CHARS < len(body):
                cut = piece.rfind(". ")
                piece = piece[: cut + 1] if cut > MAX_CHARS // 2 else piece
            chunks.append(Chunk(doc_id, title, section, f"{title} - {section}\n{piece.strip()}"))
            if start + len(piece) >= len(body):
                break
            start += max(len(piece) - OVERLAP_CHARS, 1)
    return chunks


async def ingest_directory(session: AsyncSession, directory: str) -> dict[str, int]:
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.md")):  # noqa: ASYNC240 - a few small local files
        chunks.extend(split_markdown(path.stem, path.read_text(encoding="utf-8")))

    existing = {h for (h,) in (await session.execute(select(KnowledgeChunk.content_hash))).all()}
    wanted = {c.content_hash for c in chunks}
    removed = (
        await session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.content_hash.not_in(wanted)).returning(KnowledgeChunk.id)
        )
    ).all()

    new_chunks = [c for c in chunks if c.content_hash not in existing]
    for start in range(0, len(new_chunks), 64):
        batch = new_chunks[start : start + 64]
        vectors = await gateway.embed([c.content for c in batch], purpose="ingest")
        for chunk, vector in zip(batch, vectors, strict=True):
            session.add(
                KnowledgeChunk(
                    doc_id=chunk.doc_id,
                    doc_title=chunk.doc_title,
                    section=chunk.section,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    embedding=vector,
                    tsv=func.to_tsvector("simple", chunk.content),
                )
            )
    await session.commit()
    total = (await session.execute(text("select count(*) from knowledge_chunks"))).scalar_one()
    stats = {"added": len(new_chunks), "removed": len(removed), "total": int(total)}
    log.info("knowledge_ingested", **stats)
    return stats
