"""
高级对话记忆管理系统，用于EchoMind。

特性:
- 多级记忆层次（短期、长期、语义）
- 上下文感知的记忆检索和排序
- 记忆压缩和摘要
- 用户特定的记忆隔离
- 语义搜索和关联回忆
- 记忆重要性评分和驱逐
- 跨会话记忆持久化
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import time
import threading
from collections import deque
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import chromadb
from chromadb.config import Settings
import redis

logger = logging.getLogger(__name__)

class MemoryType(Enum):
    """记忆条目类型。"""
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    SYSTEM_MESSAGE = "system_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTEXT_INFO = "context_info"
    SUMMARY = "summary"
    ENTITY = "entity"
    PREFERENCE = "preference"

class MemoryLevel(Enum):
    """记忆持久化级别。"""
    WORKING = "working"      # 当前对话上下文
    EPISODIC = "episodic"    # 会话特定记忆
    SEMANTIC = "semantic"    # 长期语义记忆
    PREFERENCE = "preference" # 用户偏好和模式

@dataclass
class MemoryEntry:
    """单个记忆条目。"""
    id: str
    user_id: str
    conversation_id: str
    memory_type: MemoryType
    memory_level: MemoryLevel
    content: str
    embedding: Optional[np.ndarray] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance_score: float = 0.5
    timestamp: datetime = field(default_factory=datetime.now)
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_compressed: bool = False
    related_memories: List[str] = field(default_factory=list)

@dataclass
class MemoryContext:
    """对话上下文窗口。"""
    user_id: str
    conversation_id: str
    current_turn: int
    total_tokens: int
    max_tokens: int
    memory_entries: List[MemoryEntry]
    context_summary: str
    relevant_entities: Dict[str, Any]
    user_preferences: Dict[str, Any]

class ConversationMemoryManager:
    """
    高级对话记忆管理，采用分层存储。

    架构:
    1. 工作记忆 - 快速访问，当前对话
    2. 情景记忆 - 会话特定，语义搜索
    3. 长期记忆 - 持久化，用户模式
    4. 偏好记忆 - 用户特定偏好和设置
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        chroma_persist_directory: str = "./data/chroma",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_working_memory: int = 50,
        max_context_tokens: int = 8000,
        memory_ttl_hours: int = 720  # 30天
    ):
        # 初始化嵌入模型
        self.embedding_model = SentenceTransformer(embedding_model)

        # 初始化Redis用于工作记忆
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.max_working_memory = max_working_memory

        # 初始化ChromaDB用于长期记忆
        self.chroma_client = chromadb.PersistentClient(
            path=chroma_persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )

        # 创建集合
        self.episodic_collection = self.chroma_client.get_or_create_collection(
            name="episodic_memory",
            metadata={"description": "会话特定记忆"}
        )

        self.semantic_collection = self.chroma_client.get_or_create_collection(
            name="semantic_memory",
            metadata={"description": "长期语义记忆"}
        )

        self.preference_collection = self.chroma_client.get_or_create_collection(
            name="user_preferences",
            metadata={"description": "用户偏好和模式"}
        )

        # 配置
        self.max_context_tokens = max_context_tokens
        self.memory_ttl = timedelta(hours=memory_ttl_hours)

        # 高频数据的内存缓存
        self.cache: Dict[str, Tuple[MemoryEntry, datetime]] = {}
        self.cache_lock = threading.Lock()
        self.max_cache_size = 1000

        # 记忆压缩设置
        self.compression_threshold = 10  # N轮后压缩
        self.compression_ratio = 0.3     # 保留前30%重要记忆

        logger.info("对话记忆管理器初始化完成")

    async def add_memory(
        self,
        user_id: str,
        conversation_id: str,
        memory_type: MemoryType,
        content: str,
        memory_level: MemoryLevel = MemoryLevel.WORKING,
        metadata: Optional[Dict[str, Any]] = None,
        importance_score: Optional[float] = None
    ) -> str:
        """
        向系统添加记忆条目。

        参数:
            user_id: 用户标识符
            conversation_id: 对话标识符
            memory_type: 记忆类型
            content: 记忆内容
            memory_level: 记忆持久化级别
            metadata: 额外元数据
            importance_score: 手动重要性评分（0-1）

        返回:
            记忆条目ID
        """
        # 生成唯一ID
        memory_id = self._generate_memory_id(user_id, conversation_id, memory_type)

        # 如果未提供则计算重要性
        if importance_score is None:
            importance_score = self._calculate_importance(content, memory_type, metadata)

        # 生成嵌入
        embedding = self.embedding_model.encode(content)

        # 创建记忆条目
        memory_entry = MemoryEntry(
            id=memory_id,
            user_id=user_id,
            conversation_id=conversation_id,
            memory_type=memory_type,
            memory_level=memory_level,
            content=content,
            embedding=embedding,
            metadata=metadata or {},
            importance_score=importance_score,
            timestamp=datetime.now()
        )

        # 在适当的记忆级别存储
        await self._store_memory(memory_entry)

        logger.debug(f"已添加记忆: {memory_id} ({memory_type.value})")
        return memory_id

    async def _store_memory(self, memory_entry: MemoryEntry):
        """在适当的存储后端存储记忆条目。"""
        if memory_entry.memory_level == MemoryLevel.WORKING:
            await self._store_working_memory(memory_entry)
        elif memory_entry.memory_level == MemoryLevel.EPISODIC:
            await self._store_episodic_memory(memory_entry)
        elif memory_entry.memory_level == MemoryLevel.SEMANTIC:
            await self._store_semantic_memory(memory_entry)
        elif memory_entry.memory_level == MemoryLevel.PREFERENCE:
            await self._store_preference_memory(memory_entry)

    async def _store_working_memory(self, memory_entry: MemoryEntry):
        """在Redis工作记忆中存储。"""
        key = f"working:{memory_entry.user_id}:{memory_entry.conversation_id}"
        data = {
            'id': memory_entry.id,
            'type': memory_entry.memory_type.value,
            'content': memory_entry.content,
            'metadata': json.dumps(memory_entry.metadata),
            'importance': memory_entry.importance_score,
            'timestamp': memory_entry.timestamp.isoformat()
        }

        # 添加到Redis列表
        self.redis_client.lpush(key, json.dumps(data))

        # 修剪到最大大小
        self.redis_client.ltrim(key, 0, self.max_working_memory - 1)

        # 设置过期时间
        self.redis_client.expire(key, int(self.memory_ttl.total_seconds()))

    async def _store_episodic_memory(self, memory_entry: MemoryEntry):
        """在ChromaDB情景集合中存储。"""
        self.episodic_collection.add(
            ids=[memory_entry.id],
            embeddings=[memory_entry.embedding.tolist()],
            documents=[memory_entry.content],
            metadatas=[{
                'user_id': memory_entry.user_id,
                'conversation_id': memory_entry.conversation_id,
                'type': memory_entry.memory_type.value,
                'level': memory_entry.memory_level.value,
                'importance': memory_entry.importance_score,
                'timestamp': memory_entry.timestamp.isoformat(),
                **memory_entry.metadata
            }]
        )

    async def _store_semantic_memory(self, memory_entry: MemoryEntry):
        """在ChromaDB语义集合中存储。"""
        self.semantic_collection.add(
            ids=[memory_entry.id],
            embeddings=[memory_entry.embedding.tolist()],
            documents=[memory_entry.content],
            metadatas=[{
                'user_id': memory_entry.user_id,
                'type': memory_entry.memory_type.value,
                'level': memory_entry.memory_level.value,
                'importance': memory_entry.importance_score,
                'timestamp': memory_entry.timestamp.isoformat(),
                **memory_entry.metadata
            }]
        )

    async def _store_preference_memory(self, memory_entry: MemoryEntry):
        """在ChromaDB偏好集合中存储。"""
        self.preference_collection.add(
            ids=[memory_entry.id],
            embeddings=[memory_entry.embedding.tolist()],
            documents=[memory_entry.content],
            metadatas=[{
                'user_id': memory_entry.user_id,
                'type': memory_entry.memory_type.value,
                'level': memory_entry.memory_level.value,
                'timestamp': memory_entry.timestamp.isoformat(),
                **memory_entry.metadata
            }]
        )

    async def get_conversation_context(
        self,
        user_id: str,
        conversation_id: str,
        max_tokens: Optional[int] = None
    ) -> MemoryContext:
        """
        获取全面的对话上下文。

        参数:
            user_id: 用户标识符
            conversation_id: 对话标识符
            max_tokens: 上下文中的最大token数

        返回:
            MemoryContext 包含所有相关信息
        """
        max_tokens = max_tokens or self.max_context_tokens

        # 获取工作记忆
        working_memories = await self._get_working_memory(user_id, conversation_id)

        # 获取相关情景记忆
        episodic_memories = await self._get_relevant_episodic_memories(
            user_id, conversation_id, working_memories[-5:] if working_memories else []
        )

        # 获取用户偏好
        preferences = await self._get_user_preferences(user_id)

        # 获取相关实体
        entities = await self._extract_entities(working_memories)

        # 构建上下文摘要
        context_summary = await self._build_context_summary(
            working_memories, episodic_memories
        )

        # 合并和排序记忆
        all_memories = working_memories + episodic_memories
        ranked_memories = self._rank_memories(all_memories, max_tokens)

        # 计算token数量
        total_tokens = self._estimate_tokens(ranked_memories)

        return MemoryContext(
            user_id=user_id,
            conversation_id=conversation_id,
            current_turn=len(working_memories),
            total_tokens=total_tokens,
            max_tokens=max_tokens,
            memory_entries=ranked_memories,
            context_summary=context_summary,
            relevant_entities=entities,
            user_preferences=preferences
        )

    async def _get_working_memory(
        self,
        user_id: str,
        conversation_id: str
    ) -> List[MemoryEntry]:
        """从Redis检索工作记忆。"""
        key = f"working:{user_id}:{conversation_id}"
        memories = []

        try:
            raw_memories = self.redis_client.lrange(key, 0, -1)
            for raw_memory in reversed(raw_memories):  # 按时间顺序获取
                data = json.loads(raw_memory)
                memory_entry = MemoryEntry(
                    id=data['id'],
                    user_id=user_id,
                    conversation_id=conversation_id,
                    memory_type=MemoryType(data['type']),
                    memory_level=MemoryLevel.WORKING,
                    content=data['content'],
                    metadata=json.loads(data['metadata']),
                    importance_score=data['importance'],
                    timestamp=datetime.fromisoformat(data['timestamp'])
                )
                memories.append(memory_entry)
        except Exception as e:
            logger.error(f"检索工作记忆错误: {e}")

        return memories

    async def _get_relevant_episodic_memories(
        self,
        user_id: str,
        conversation_id: str,
        recent_memories: List[MemoryEntry],
        limit: int = 10
    ) -> List[MemoryEntry]:
        """使用语义搜索获取相关情景记忆。"""
        if not recent_memories:
            return []

        # 使用最近记忆作为查询
        query_texts = [mem.content for mem in recent_memories[-3:]]
        query_embeddings = self.embedding_model.encode(query_texts)

        # 在情景集合中搜索
        results = self.episodic_collection.query(
            query_embeddings=query_embeddings.tolist(),
            n_results=limit,
            where={
                "user_id": user_id,
                "conversation_id": {"$ne": conversation_id}  # 排除当前对话
            }
        )

        # 转换为MemoryEntry对象
        memories = []
        for i, (doc_id, document, metadata) in enumerate(zip(
            results['ids'][0],
            results['documents'][0],
            results['metadatas'][0]
        )):
            memory_entry = MemoryEntry(
                id=doc_id,
                user_id=metadata['user_id'],
                conversation_id=metadata['conversation_id'],
                memory_type=MemoryType(metadata['type']),
                memory_level=MemoryLevel.EPISODIC,
                content=document,
                metadata={k: v for k, v in metadata.items()
                         if k not in ['user_id', 'conversation_id', 'type', 'level', 'importance', 'timestamp']},
                importance_score=metadata.get('importance', 0.5),
                timestamp=datetime.fromisoformat(metadata['timestamp'])
            )
            memories.append(memory_entry)

        return memories

    async def _get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """获取用户偏好和模式。"""
        try:
            results = self.preference_collection.query(
                query_texts=["用户偏好"],
                n_results=20,
                where={"user_id": user_id}
            )

            preferences = {}
            for metadata in results['metadatas'][0]:
                pref_type = metadata.get('type', 'preference')
                if pref_type not in preferences:
                    preferences[pref_type] = []
                preferences[pref_type].append(metadata)

            return preferences
        except Exception as e:
            logger.error(f"检索用户偏好错误: {e}")
            return {}

    async def _extract_entities(self, memories: List[MemoryEntry]) -> Dict[str, Any]:
        """从记忆中提取实体。"""
        entities = {
            'mentioned': [],
            'frequent': {},
            'recent': []
        }

        for memory in memories[-10:]:  # 最近10条记忆
            if memory.metadata.get('entities'):
                entities['mentioned'].extend(memory.metadata['entities'])

        # 统计频繁实体
        for entity in entities['mentioned']:
            if isinstance(entity, str):
                entities['frequent'][entity] = entities['frequent'].get(entity, 0) + 1

        return entities

    async def _build_context_summary(
        self,
        working_memories: List[MemoryEntry],
        episodic_memories: List[MemoryEntry]
    ) -> str:
        """构建对话上下文的摘要。"""
        summary_parts = []

        if working_memories:
            summary_parts.append(f"当前对话有 {len(working_memories)} 轮")

        if episodic_memories:
            summary_parts.append(f"发现 {len(episodic_memories)} 条相关历史交互")

        # 从最近记忆获取关键主题
        if working_memories:
            recent_content = " ".join([mem.content for mem in working_memories[-5:]])
            summary_parts.append(f"最近主题: {recent_content[:200]}...")

        return ". ".join(summary_parts)

    def _rank_memories(
        self,
        memories: List[MemoryEntry],
        max_tokens: int
    ) -> List[MemoryEntry]:
        """按重要性和相关性对记忆进行排序。"""
        # 计算综合得分
        for memory in memories:
            time_decay = self._calculate_time_decay(memory.timestamp)
            recency_boost = 1.0 / (1.0 + time_decay)
            composite_score = (
                memory.importance_score * 0.6 +
                recency_boost * 0.3 +
                (memory.access_count / 10.0) * 0.1
            )
            memory.metadata['composite_score'] = composite_score

        # 按综合得分排序
        ranked = sorted(memories, key=lambda m: m.metadata.get('composite_score', 0), reverse=True)

        # 按token限制筛选
        selected = []
        current_tokens = 0
        for memory in ranked:
            memory_tokens = self._estimate_memory_tokens(memory)
            if current_tokens + memory_tokens <= max_tokens:
                selected.append(memory)
                current_tokens += memory_tokens
            else:
                break

        return selected

    def _calculate_time_decay(self, timestamp: datetime) -> float:
        """计算时间衰减因子。"""
        age_hours = (datetime.now() - timestamp).total_seconds() / 3600
        return age_hours / 24.0  # 按天衰减

    def _estimate_memory_tokens(self, memory: MemoryEntry) -> int:
        """估计记忆条目的token数量。"""
        return len(memory.content.split()) * 1.3  # 粗略估计

    def _estimate_tokens(self, memories: List[MemoryEntry]) -> int:
        """估计记忆中的总token数。"""
        return sum(self._estimate_memory_tokens(mem) for mem in memories)

    async def search_memories(
        self,
        user_id: str,
        query: str,
        memory_types: Optional[List[MemoryType]] = None,
        limit: int = 10,
        date_range: Optional[Tuple[datetime, datetime]] = None
    ) -> List[MemoryEntry]:
        """
        使用语义相似度搜索记忆。

        参数:
            user_id: 用户标识符
            query: 搜索查询
            memory_types: 按记忆类型筛选
            limit: 最大结果数
            date_range: 可选日期范围筛选

        返回:
            匹配的记忆条目列表
        """
        query_embedding = self.embedding_model.encode([query])

        # 构建筛选条件
        where_filter = {"user_id": user_id}
        if memory_types:
            where_filter["type"] = {"$in": [mt.value for mt in memory_types]}

        # 在两个集合中搜索
        episodic_results = self.episodic_collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=limit,
            where=where_filter
        )

        semantic_results = self.semantic_collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=limit,
            where=where_filter
        )

        # 合并和去重结果
        all_results = []
        seen_ids = set()

        for results in [episodic_results, semantic_results]:
            for i, (doc_id, document, metadata) in enumerate(zip(
                results['ids'][0],
                results['documents'][0],
                results['metadatas'][0]
            )):
                if doc_id not in seen_ids:
                    # 应用日期筛选（如果指定）
                    if date_range:
                        timestamp = datetime.fromisoformat(metadata['timestamp'])
                        if not (date_range[0] <= timestamp <= date_range[1]):
                            continue

                    memory_entry = MemoryEntry(
                        id=doc_id,
                        user_id=metadata['user_id'],
                        conversation_id=metadata.get('conversation_id', ''),
                        memory_type=MemoryType(metadata['type']),
                        memory_level=MemoryLevel(metadata.get('level', 'episodic')),
                        content=document,
                        metadata={k: v for k, v in metadata.items()
                                 if k not in ['user_id', 'type', 'level', 'timestamp']},
                        importance_score=metadata.get('importance', 0.5),
                        timestamp=datetime.fromisoformat(metadata['timestamp'])
                    )
                    all_results.append(memory_entry)
                    seen_ids.add(doc_id)

        return all_results[:limit]

    async def compress_conversation(
        self,
        user_id: str,
        conversation_id: str,
        keep_ratio: float = 0.3
    ) -> str:
        """
        压缩对话记忆，仅保留重要内容。

        参数:
            user_id: 用户标识符
            conversation_id: 对话标识符
            keep_ratio: 保留记忆的比例

        返回:
            压缩对话的摘要
        """
        working_memories = await self._get_working_memory(user_id, conversation_id)

        if len(working_memories) < self.compression_threshold:
            return "对话太短无法压缩"

        # 按重要性对记忆排序
        ranked = sorted(
            working_memories,
            key=lambda m: m.importance_score,
            reverse=True
        )

        # 保留顶部记忆
        keep_count = max(1, int(len(ranked) * keep_ratio))
        kept_memories = ranked[:keep_count]

        # 创建摘要
        summary = self._create_conversation_summary(kept_memories)

        # 将摘要存储为语义记忆
        await self.add_memory(
            user_id=user_id,
            conversation_id=conversation_id,
            memory_type=MemoryType.SUMMARY,
            content=summary,
            memory_level=MemoryLevel.SEMANTIC,
            metadata={
                'original_turn_count': len(working_memories),
                'compressed_turn_count': len(kept_memories),
                'compression_ratio': keep_ratio
            }
        )

        # 清除工作记忆
        key = f"working:{user_id}:{conversation_id}"
        self.redis_client.delete(key)

        # 将保留的记忆重新添加
        for memory in kept_memories:
            await self._store_working_memory(memory)

        return summary

    def _create_conversation_summary(self, memories: List[MemoryEntry]) -> str:
        """创建对话记忆的摘要。"""
        if not memories:
            return ""

        # 提取关键信息
        user_messages = [m for m in memories if m.memory_type == MemoryType.USER_MESSAGE]
        assistant_messages = [m for m in memories if m.memory_type == MemoryType.ASSISTANT_MESSAGE]

        summary_parts = []
        summary_parts.append(f"包含 {len(user_messages)} 条用户消息和 {len(assistant_messages)} 条助手回复的对话")

        if user_messages:
            # 从用户消息获取关键主题
            all_user_content = " ".join([m.content for m in user_messages])
            summary_parts.append(f"讨论的主要主题: {all_user_content[:300]}...")

        if assistant_messages:
            # 检查问题是否已解决
            last_assistant = assistant_messages[-1]
            if "resolved" in last_assistant.content.lower() or "solved" in last_assistant.content.lower():
                summary_parts.append("问题已标记为已解决")

        return ". ".join(summary_parts)

    def _calculate_importance(
        self,
        content: str,
        memory_type: MemoryType,
        metadata: Optional[Dict[str, Any]]
    ) -> float:
        """计算记忆的重要性评分。"""
        importance = 0.5  # 基础重要性

        # 根据记忆类型调整
        type_importance = {
            MemoryType.USER_MESSAGE: 0.7,
            MemoryType.ASSISTANT_MESSAGE: 0.5,
            MemoryType.TOOL_CALL: 0.4,
            MemoryType.TOOL_RESULT: 0.4,
            MemoryType.ENTITY: 0.8,
            MemoryType.PREFERENCE: 0.9,
            MemoryType.SUMMARY: 0.6
        }
        importance *= type_importance.get(memory_type, 0.5)

        # 根据内容调整
        if any(keyword in content.lower() for keyword in
               ['important', 'urgent', 'critical', 'error', 'issue', 'problem']):
            importance += 0.2

        if any(keyword in content.lower() for keyword in
               ['thank', 'thanks', 'great', 'awesome', 'helpful']):
            importance += 0.1

        # 根据元数据调整
        if metadata and metadata.get('sentiment') == 'negative':
            importance += 0.2
        if metadata and metadata.get('resolved'):
            importance += 0.1

        return min(1.0, max(0.0, importance))

    def _generate_memory_id(
        self,
        user_id: str,
        conversation_id: str,
        memory_type: MemoryType
    ) -> str:
        """生成唯一记忆ID。"""
        timestamp = datetime.now().isoformat()
        unique_string = f"{user_id}:{conversation_id}:{memory_type.value}:{timestamp}"
        return hashlib.md5(unique_string.encode()).hexdigest()[:16]

    async def clear_conversation_memory(self, user_id: str, conversation_id: str):
        """清除特定对话的所有记忆。"""
        # 清除工作记忆
        key = f"working:{user_id}:{conversation_id}"
        self.redis_client.delete(key)

        # 清除情景记忆
        try:
            episodic_ids = self.episodic_collection.get(
                where={"user_id": user_id, "conversation_id": conversation_id}
            )['ids']
            if episodic_ids:
                self.episodic_collection.delete(ids=episodic_ids)
        except Exception as e:
            logger.error(f"清除情景记忆错误: {e}")

        logger.info(f"已清除对话 {conversation_id} 的记忆")

    async def get_memory_stats(self, user_id: str) -> Dict[str, Any]:
        """获取用户的记忆统计信息。"""
        stats = {
            'user_id': user_id,
            'working_memory_count': 0,
            'episodic_memory_count': 0,
            'semantic_memory_count': 0,
            'preference_count': 0,
            'total_memories': 0
        }

        try:
            # 统计工作记忆
            keys = self.redis_client.keys(f"working:{user_id}:*")
            for key in keys:
                stats['working_memory_count'] += self.redis_client.llen(key)

            # 统计情景记忆
            episodic_results = self.episodic_collection.get(
                where={"user_id": user_id}
            )
            stats['episodic_memory_count'] = len(episodic_results['ids'])

            # 统计语义记忆
            semantic_results = self.semantic_collection.get(
                where={"user_id": user_id}
            )
            stats['semantic_memory_count'] = len(semantic_results['ids'])

            # 统计偏好
            preference_results = self.preference_collection.get(
                where={"user_id": user_id}
            )
            stats['preference_count'] = len(preference_results['ids'])

            stats['total_memories'] = (
                stats['working_memory_count'] +
                stats['episodic_memory_count'] +
                stats['semantic_memory_count'] +
                stats['preference_count']
            )

        except Exception as e:
            logger.error(f"获取记忆统计错误: {e}")

        return stats