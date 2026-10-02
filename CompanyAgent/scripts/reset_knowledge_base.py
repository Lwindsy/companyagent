"""Replace the RAG knowledge base with the checked-in English demo documents.

Run inside the deployed companyagent container. This replaces only the
knowledge_base Chroma collection; conversation memory and user profiles remain.
"""
import json
import os
import pathlib
import sys

from dotenv import load_dotenv


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from mcp.knowledge_base import KnowledgeBase  # noqa: E402


def main() -> None:
    source_path = ROOT / "data" / "demo_docs" / "sample_knowledge.json"
    documents = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(documents, list) or not all(
        isinstance(item, dict) and item.get("title") and item.get("content")
        for item in documents
    ):
        raise ValueError("English demo knowledge file is invalid")

    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    count = kb.replace_documents(documents)
    print(f"Replaced knowledge_base with {count} English document chunks.")


if __name__ == "__main__":
    main()
