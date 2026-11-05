from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


HEADING = re.compile(r"^#{1,3}\s+(.+?)\s*$")
ASCII_TERM = re.compile(r"[a-zA-Z0-9.+-]+")
CJK = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    source: str
    title: str
    text: str


class MarkdownKnowledgeProvider:
    """Retrieves only customer-safe facts from the v2 knowledge directory."""

    def __init__(self, knowledge_path: str | Path) -> None:
        self.knowledge_path = Path(knowledge_path)
        self.chunks = self._load_chunks()

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        query_terms = _terms(query)
        if not query_terms:
            return []
        ranked: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self.chunks:
            title_terms = _terms(chunk.title)
            body_terms = _terms(chunk.text)
            title_hits = len(query_terms & title_terms)
            body_hits = len(query_terms & body_terms)
            score = title_hits * 4 + body_hits
            if score:
                ranked.append((float(score), chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].source, item[1].title))
        return [
            {"source": chunk.source, "title": chunk.title, "text": chunk.text}
            for _, chunk in ranked[:limit]
        ]

    def _load_chunks(self) -> tuple[KnowledgeChunk, ...]:
        chunks: list[KnowledgeChunk] = []
        if not self.knowledge_path.exists():
            return ()
        for path in sorted(self.knowledge_path.glob("*.md")):
            chunks.extend(_split_markdown(path))
        return tuple(chunks)


def _split_markdown(path: Path) -> list[KnowledgeChunk]:
    title = path.stem
    lines: list[str] = []
    chunks: list[KnowledgeChunk] = []

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            chunks.append(
                KnowledgeChunk(
                    source=path.name,
                    title=title,
                    text=text[:2400],
                )
            )
        lines.clear()

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(raw_line)
        if match:
            flush()
            title = match.group(1).strip()
        else:
            lines.append(raw_line)
    flush()
    return chunks


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = {item.lower() for item in ASCII_TERM.findall(lowered) if item.strip()}
    cjk_chars = [char for char in lowered if CJK.match(char)]
    terms.update(cjk_chars)
    terms.update("".join(cjk_chars[index : index + 2]) for index in range(len(cjk_chars) - 1))
    return terms
