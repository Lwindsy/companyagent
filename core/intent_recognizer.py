"""
端到端意图识别系统，采用多模态理解技术。
本模块提供全面的意图检测功能，结合以下技术：
- 基于LLM的语义理解
- 上下文感知分析
- 多维度特征提取
- 持续学习能力
"""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from anthropic import AsyncAnthropic
import json
import time

logger = logging.getLogger(__name__)

class IntentCategory(Enum):
    """客服意图分类。"""
    QUERY = "query"
    COMPLAINT = "complaint"
    REQUEST = "request"
    GREETING = "greeting"
    ESCALATION = "escalation"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT = "account"
    FEEDBACK = "feedback"
    OTHER = "other"

class UrgencyLevel(Enum):
    """紧急程度级别，用于优先级排序。"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

@dataclass
class IntentResult:
    """意图识别结果。"""
    intent: IntentCategory
    confidence: float
    urgency: UrgencyLevel
    entities: Dict[str, Any]
    sub_intents: List[Tuple[str, float]]
    reasoning: str
    metadata: Dict[str, Any]

class IntentRecognizer:
    """
    端到端意图识别，采用多模态理解技术。

    核心特性：
    1. 基于LLM的语义理解
    2. 基于嵌入的相似度匹配
    3. 上下文感知的意图解析
    4. 从交互中持续学习
    5. 紧急度检测和优先级排序
    """

    def __init__(
        self,
        anthropic_api_key: str,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        cache_size: int = 1000,
        confidence_threshold: float = 0.6
    ):
        self.anthropic = AsyncAnthropic(api_key=anthropic_api_key)
        self.embedding_model = SentenceTransformer(embedding_model)
        self.confidence_threshold = confidence_threshold

        # Few-shot学习的意图模板
        self.intent_templates = {
            IntentCategory.QUERY: [
                "我的订单状态是什么？",
                "如何重置密码？",
                "我的快递什么时候到？"
            ],
            IntentCategory.COMPLAINT: [
                "我已经等了几个小时！",
                "这个服务太糟糕了！",
                "没人帮我！"
            ],
            IntentCategory.REQUEST: [
                "你能帮我...",
                "我需要...",
                "请协助..."
            ],
            IntentCategory.GREETING: [
                "你好",
                "嗨",
                "早上好"
            ],
            IntentCategory.ESCALATION: [
                "我要见经理！",
                "这无法接受！",
                "我要投诉！"
            ],
            IntentCategory.TECHNICAL: [
                "应用一直崩溃",
                "我无法登录",
                "结账时出现404错误"
            ],
            IntentCategory.BILLING: [
                "为什么我被收费两次？",
                "我要退款",
                "发票#12345"
            ],
            IntentCategory.ACCOUNT: [
                "我想更新我的个人资料",
                "更改邮箱地址",
                "删除我的账户"
            ],
            IntentCategory.FEEDBACK: [
                "服务很棒！",
                "我喜欢你们的产品",
                "这是我的反馈"
            ]
        }

        # 预计算模板的嵌入向量
        self.template_embeddings = self._compute_template_embeddings()

        # 缓存以提高性能
        self.cache = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def _compute_template_embeddings(self) -> Dict[IntentCategory, np.ndarray]:
        """预计算意图模板的嵌入向量。"""
        embeddings = {}
        for intent, templates in self.intent_templates.items():
            intent_embeddings = self.embedding_model.encode(templates)
            embeddings[intent] = intent_embeddings
        return embeddings

    async def recognize_intent(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> IntentResult:
        """
        端到端意图识别，结合多种策略。

        参数:
            message: 当前用户消息
            context: 额外上下文（用户信息、会话数据等）
            conversation_history: 之前的消息用于上下文

        返回:
            IntentResult 包含全面分析
        """
        # 首先检查缓存
        cache_key = self._get_cache_key(message, context)
        if cache_key in self.cache:
            self.cache_hits += 1
            return self.cache[cache_key]
        self.cache_misses += 1

        start_time = time.time()

        # 多策略意图识别
        llm_result = await self._llm_intent_recognition(message, context, conversation_history)
        embedding_result = self._embedding_intent_recognition(message)
        pattern_result = self._pattern_intent_recognition(message)

        # 加权投票合并结果
        combined_intent = self._combine_intents(
            llm_result,
            embedding_result,
            pattern_result
        )

        # 提取实体
        entities = await self._extract_entities(message, context)

        # 确定紧急度
        urgency = self._determine_urgency(message, combined_intent, entities)

        # 生成推理说明
        reasoning = self._generate_reasoning(combined_intent, message, entities)

        result = IntentResult(
            intent=combined_intent,
            confidence=llm_result.get('confidence', 0.0),
            urgency=urgency,
            entities=entities,
            sub_intents=llm_result.get('sub_intents', []),
            reasoning=reasoning,
            metadata={
                'processing_time': time.time() - start_time,
                'llm_confidence': llm_result.get('confidence', 0.0),
                'embedding_confidence': embedding_result.get('confidence', 0.0),
                'cache_status': 'miss',
                'strategies_used': ['llm', 'embedding', 'pattern']
            }
        )

        # 更新缓存
        if len(self.cache) < 1000:
            self.cache[cache_key] = result

        return result

    def _get_cache_key(self, message: str, context: Optional[Dict] = None) -> str:
        """从消息和上下文生成缓存键。"""
        context_str = json.dumps(context, sort_keys=True) if context else ""
        return f"{message}|{context_str}"[:200]

    async def _llm_intent_recognition(
        self,
        message: str,
        context: Optional[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]]
    ) -> Dict[str, Any]:
        """
        基于LLM的意图识别，采用Few-shot学习。
        使用Anthropic Claude进行深度语义理解。
        """
        # 构建Few-shot示例
        examples = self._build_few_shot_examples()

        # 包含对话上下文
        context_str = self._format_context(context, conversation_history)

        prompt = f"""你是一个专业的客服意图分析专家。分析用户消息并确定他们的意图。

{examples}

用户消息: "{message}"

上下文: {context_str}

请按以下JSON格式提供分析：
{{
    "intent": "intent_category",
    "confidence": 0.0-1.0,
    "sub_intents": [["sub_intent", confidence], ...],
    "reasoning": "简要说明"
}}

可用意图: {', '.join([i.value for i in IntentCategory])}
"""

        try:
            response = await self.anthropic.messages.create(
                model="claude-3-5-sonnet-20240229",
                max_tokens=1024,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            content = response.content[0].text
            # 从响应中提取JSON
            json_match = self._extract_json(content)
            if json_match:
                result = json.loads(json_match)
                # 将字符串映射到枚举
                try:
                    result['intent'] = IntentCategory(result['intent'])
                except ValueError:
                    result['intent'] = IntentCategory.OTHER
                return result

        except Exception as e:
            logger.error(f"LLM意图识别失败: {e}")

        # 降级处理
        return {
            'intent': IntentCategory.OTHER,
            'confidence': 0.3,
            'sub_intents': [],
            'reasoning': 'LLM识别失败，使用降级方案'
        }

    def _build_few_shot_examples(self) -> str:
        """为LLM构建Few-shot示例。"""
        examples = []
        for intent, templates in self.intent_templates.items():
            for template in templates[:2]:  # 每个意图使用前2个示例
                examples.append(f'消息: "{template}"')
                examples.append(f'意图: {intent.value}')
                examples.append('')

        return "以下是一些示例:\n\n" + "\n".join(examples)

    def _format_context(
        self,
        context: Optional[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]]
    ) -> str:
        """格式化上下文用于LLM提示。"""
        context_info = []

        if context:
            for key, value in context.items():
                context_info.append(f"{key}: {value}")

        if conversation_history:
            context_info.append("最近对话:")
            for msg in conversation_history[-3:]:  # 最近3条消息
                context_info.append(f"{msg.get('role', 'user')}: {msg.get('content', '')}")

        return "\n".join(context_info) if context_info else "无额外上下文"

    def _extract_json(self, text: str) -> Optional[str]:
        """从LLM响应中提取JSON。"""
        try:
            start = text.find('{')
            end = text.rfind('}') + 1
            if start >= 0 and end > start:
                return text[start:end]
        except:
            pass
        return None

    def _embedding_intent_recognition(self, message: str) -> Dict[str, Any]:
        """
        基于嵌入的意图识别，使用语义相似度。
        对常见模式快速高效。
        """
        message_embedding = self.embedding_model.encode([message])[0]

        best_intent = IntentCategory.OTHER
        best_score = 0.0

        for intent, template_embeddings in self.template_embeddings.items():
            # 计算与所有模板的相似度
            similarities = cosine_similarity(
                [message_embedding],
                template_embeddings
            )[0]

            # 使用最大相似度
            max_similarity = np.max(similarities)

            if max_similarity > best_score:
                best_score = max_similarity
                best_intent = intent

        return {
            'intent': best_intent,
            'confidence': float(best_score),
            'method': 'embedding_similarity'
        }

    def _pattern_intent_recognition(self, message: str) -> Dict[str, Any]:
        """
        基于模式的意图识别，使用关键词和启发式规则。
        对明显模式的快速降级处理。
        """
        message_lower = message.lower()

        patterns = {
            IntentCategory.ESCALATION: ['manager', 'supervisor', 'terrible', 'unacceptable', 'report'],
            IntentCategory.COMPLAINT: ['terrible', 'horrible', 'worst', 'never', 'waiting'],
            IntentCategory.QUERY: ['?', 'how', 'what', 'when', 'where', 'why', 'status'],
            IntentCategory.REQUEST: ['please', 'can you', 'could you', 'need', 'help'],
            IntentCategory.GREETING: ['hello', 'hi', 'hey', 'good morning', 'good afternoon'],
            IntentCategory.BILLING: ['charge', 'refund', 'invoice', 'payment', 'billing'],
            IntentCategory.TECHNICAL: ['error', 'crash', 'broken', 'not working', 'login'],
            IntentCategory.ACCOUNT: ['account', 'profile', 'password', 'email', 'delete']
        }

        matched_patterns = {}
        for intent, keywords in patterns.items():
            matches = sum(1 for keyword in keywords if keyword in message_lower)
            if matches > 0:
                matched_patterns[intent] = matches / len(keywords)

        if matched_patterns:
            best_intent = max(matched_patterns, key=matched_patterns.get)
            confidence = matched_patterns[best_intent]
        else:
            best_intent = IntentCategory.OTHER
            confidence = 0.0

        return {
            'intent': best_intent,
            'confidence': confidence,
            'method': 'pattern_matching'
        }

    def _combine_intents(
        self,
        llm_result: Dict,
        embedding_result: Dict,
        pattern_result: Dict
    ) -> IntentCategory:
        """
        合并多种识别策略的结果。
        使用置信度加权投票。
        """
        # 不同策略的权重
        weights = {
            'llm': 0.5,
            'embedding': 0.3,
            'pattern': 0.2
        }

        # 带权重的投票计数
        votes = {}

        # LLM投票
        llm_intent = llm_result.get('intent', IntentCategory.OTHER)
        llm_confidence = llm_result.get('confidence', 0.0)
        votes[llm_intent] = votes.get(llm_intent, 0) + weights['llm'] * llm_confidence

        # 嵌入投票
        embedding_intent = embedding_result.get('intent', IntentCategory.OTHER)
        embedding_confidence = embedding_result.get('confidence', 0.0)
        votes[embedding_intent] = votes.get(embedding_intent, 0) + weights['embedding'] * embedding_confidence

        # 模式投票
        pattern_intent = pattern_result.get('intent', IntentCategory.OTHER)
        pattern_confidence = pattern_result.get('confidence', 0.0)
        votes[pattern_intent] = votes.get(pattern_intent, 0) + weights['pattern'] * pattern_confidence

        # 找出获胜者
        if votes:
            best_intent = max(votes, key=votes.get)
            # 仅当置信度高于阈值时返回
            if votes[best_intent] >= self.confidence_threshold:
                return best_intent

        return IntentCategory.OTHER

    async def _extract_entities(
        self,
        message: str,
        context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        从用户消息中提取实体。
        使用LLM进行稳健的实体提取。
        """
        prompt = f"""从此客服消息中提取实体:
消息: "{message}"

提取以下类型的实体:
- order_id: 订单或交易号
- product_name: 产品或服务名称
- dates: 任何日期或时间引用
- amounts: 金额或数量值
- contact_info: 邮箱地址、电话号码
- technical_terms: 技术术语、错误代码

返回JSON格式:
{{
    "entities": {{
        "order_id": [],
        "product_name": [],
        "dates": [],
        "amounts": [],
        "contact_info": [],
        "technical_terms": []
    }}
}}
"""

        try:
            response = await self.anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=512,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )

            content = response.content[0].text
            json_match = self._extract_json(content)
            if json_match:
                result = json.loads(json_match)
                return result.get('entities', {})

        except Exception as e:
            logger.error(f"实体提取失败: {e}")

        return {
            'order_id': [],
            'product_name': [],
            'dates': [],
            'amounts': [],
            'contact_info': [],
            'technical_terms': []
        }

    def _determine_urgency(
        self,
        message: str,
        intent: IntentCategory,
        entities: Dict[str, Any]
    ) -> UrgencyLevel:
        """
        基于意图、内容和实体确定紧急程度。
        """
        urgency_keywords = {
            UrgencyLevel.CRITICAL: ['emergency', 'urgent', 'asap', 'immediately', 'critical'],
            UrgencyLevel.HIGH: ['as soon as possible', 'hurry', 'need it now', 'today'],
            UrgencyLevel.MEDIUM: ['this week', 'soon', 'quick question'],
            UrgencyLevel.LOW: ['whenever', 'no rush', 'eventually']
        }

        message_lower = message.lower()

        # 检查紧急关键词
        for urgency_level, keywords in urgency_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                return urgency_level

        # 基于意图的紧急程度
        if intent == IntentCategory.ESCALATION:
            return UrgencyLevel.HIGH
        elif intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM

        # 带错误代码的技术问题是高优先级
        if entities.get('technical_terms') and intent == IntentCategory.TECHNICAL:
            return UrgencyLevel.MEDIUM

        return UrgencyLevel.LOW

    def _generate_reasoning(
        self,
        intent: IntentCategory,
        message: str,
        entities: Dict[str, Any]
    ) -> str:
        """生成意图分类的人类可读推理说明。"""
        reasoning_parts = [
            f"基于消息内容和上下文分类为 {intent.value}"
        ]

        if entities:
            found_entities = [f"{k}: {v}" for k, v in entities.items() if v]
            if found_entities:
                reasoning_parts.append(f"发现实体: {', '.join(found_entities)}")

        return ". ".join(reasoning_parts)

    def learn_from_feedback(
        self,
        message: str,
        predicted_intent: IntentCategory,
        correct_intent: IntentCategory,
        feedback: str
    ):
        """
        从人工反馈中学习以改进未来预测。
        实现持续学习。
        """
        if predicted_intent != correct_intent:
            logger.info(f"从反馈中学习: {predicted_intent} -> {correct_intent}")

            # 添加到意图模板供将来参考
            if correct_intent not in self.intent_templates:
                self.intent_templates[correct_intent] = []

            if message not in self.intent_templates[correct_intent]:
                self.intent_templates[correct_intent].append(message)

                # 更新嵌入向量
                self.template_embeddings = self._compute_template_embeddings()

                logger.info(f"已将消息添加到 {correct_intent} 模板并更新嵌入向量")

    def get_cache_stats(self) -> Dict[str, int]:
        """获取缓存统计信息用于监控。"""
        total_requests = self.cache_hits + self.cache_misses
        hit_rate = self.cache_hits / total_requests if total_requests > 0 else 0

        return {
            'cache_size': len(self.cache),
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'hit_rate': hit_rate,
            'total_requests': total_requests
        }

    def clear_cache(self):
        """清除意图识别缓存。"""
        self.cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        logger.info("意图识别缓存已清除")