"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（自动生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List

import chromadb

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    ChromaDB 内置了 Embedding 模型（all-MiniLM-L6-v2），
    调用 add() 时自动生成向量，query() 时自动做语义匹配。
    不需要额外调用 Anthropic Embeddings API。
    """

    COLLECTION_NAME = "knowledge_base"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"Knowledge-base ChromaDB connected: {chroma_host}:{chroma_port}")
        except Exception:
            logger.info(f"Knowledge-base ChromaDB service unavailable; using local mode: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 使用服务端时不传 embedding_function，让服务端处理
        # 本地模式时也不传，使用 ChromaDB 默认的（会触发模型下载）
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Customer-support RAG knowledge base"},
        )

        # 如果知识库为空，导入默认文档
        if self._collection.count() == 0:
            self._load_default_docs()

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "..."}, ...]
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            chunks  = self._chunk_text(content, chunk_size=500)

            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{title}_{i}_{chunk[:50]}".encode()).hexdigest()
                ids.append(doc_id)
                docs.append(chunk)
                metas.append({"title": title, "chunk_index": i, "total_chunks": len(chunks)})

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.add(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"Imported {len(ids)} knowledge-base document chunks")

        return len(ids)

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """异步导入文档；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.add_documents, documents)

    def replace_documents(self, documents: List[Dict[str, str]]) -> int:
        """Replace only the RAG knowledge-base collection with new documents.

        Conversation memory and user-profile collections are separate and are not
        touched by this operation.
        """
        self._client.delete_collection(self.COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Customer-support RAG knowledge base"},
        )
        return self.add_documents(documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "title":    meta.get("title", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),
                })

        return items

    async def search_async(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """异步检索；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """异步获取文档片段数量。"""
        return await asyncio.to_thread(self._collection.count)

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return await self.search_async(query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        default_docs = [
            {
                "title": "Refund Policy",
                "content": (
                    "Refund policy overview. "
                    "Customers may request a no-reason refund within 7 days of purchase. "
                    "Refund requests are reviewed within 1–3 business days. "
                    "Once approved, funds are returned to the original payment account within 5–7 business days. "
                    "If an item has shipped, the return process must be completed before a refund can be issued. "
                    "Return shipping is paid by the customer unless the item has a quality defect. "
                    "For orders older than 7 but no more than 30 days, evidence of a product-quality issue is required."
                ),
            },
            {
                "title": "Order Lookup",
                "content": (
                    "Order lookup guide. "
                    "Customers can check an order's status with its order number. "
                    "Statuses include pending payment, paid, shipped, in transit, delivered, and completed. "
                    "If an order is marked shipped but has not arrived after 7 days, contact support for a shipment trace. "
                    "Tracking information is usually updated within 24 hours after shipment. "
                    "If an order shows an exception, provide the order number to support."
                ),
            },
            {
                "title": "Account Security",
                "content": (
                    "Account security guidance. "
                    "Change passwords regularly; passwords should contain at least 8 characters, including letters and numbers. "
                    "If a password is forgotten, reset it through the linked phone number or email address. "
                    "When suspicious sign-in activity is detected, the system locks the account and sends a notification. "
                    "Enable two-factor authentication in Security Settings for better account protection. "
                    "Never share your password. Support staff will never ask for it."
                ),
            },
            {
                "title": "Technical Troubleshooting",
                "content": (
                    "Common technical troubleshooting. "
                    "App crash: clear the cache and restart the app; update to the latest version if the issue persists. "
                    "Login failure / 401: authentication failed; check your credentials or reset the password. "
                    "Slow page loading: check your network connection and try switching between Wi-Fi and mobile data. "
                    "Payment failure: confirm sufficient card balance and that online payments are enabled. "
                    "Server error 500: this is a server-side issue. Try again later or contact technical support if it persists."
                ),
            },
            {
                "title": "Membership and Points",
                "content": (
                    "Membership points policy. "
                    "Earn 1 point for every CNY 1 spent. "
                    "Points may be redeemed on a future purchase: 100 points = CNY 1. "
                    "Tiers are Standard, Silver (CNY 1,000 cumulative spend), and Gold (CNY 5,000 cumulative spend). "
                    "Silver members receive a 5% discount; Gold members receive a 10% discount. "
                    "Points expire after one year. "
                    "Purchases made in a member's birthday month earn double points."
                ),
            },
            {
                "title": "Delivery Information",
                "content": (
                    "Delivery service information. "
                    "Standard delivery arrives in 3–5 business days and is free for orders of CNY 99 or more. "
                    "Express delivery arrives in 1–2 business days and costs CNY 15. "
                    "Local delivery arrives the same or next day and costs CNY 10. "
                    "Remote areas may require an additional 2–3 days. "
                    "Deliveries are made daily from 9:00 to 18:00 and may be delayed on public holidays. "
                    "To change a delivery address, contact support before the order ships."
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(f"Imported {len(default_docs)} default knowledge-base documents")
