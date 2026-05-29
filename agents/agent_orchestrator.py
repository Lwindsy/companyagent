"""
EchoMind的高级多Agent编排系统。

特性:
- 基于意图和上下文的动态Agent路由
- 并行Agent执行和协调
- Agent协作和知识共享
- 降级和升级机制
- 基于性能的Agent选择
- Agent生命周期管理
- 跨Agent通信协议
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import uuid
import json
import time
from collections import defaultdict
import hashlib

from anthropic import AsyncAnthropic

from core.intent_recognizer import IntentRecognizer, IntentCategory, UrgencyLevel

logger = logging.getLogger(__name__)

class AgentType(Enum):
    """专用Agent类型。"""
    GENERAL = "general"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT = "account"
    SALES = "sales"
    SUPPORT = "support"
    ESCALATION = "escalation"
    KNOWLEDGE = "knowledge"
    ANALYSIS = "analysis"

class AgentStatus(Enum):
    """Agent运行状态。"""
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"
    MAINTENANCE = "maintenance"

class AgentCapability(Enum):
    """Agent能力。"""
    INTENT_RECOGNITION = "intent_recognition"
    TOOL_EXECUTION = "tool_execution"
    MEMORY_ACCESS = "memory_access"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    ANALYSIS = "analysis"
    COORDINATION = "coordination"
    ESCALATION = "escalation"

@dataclass
class AgentConfig:
    """Agent配置。"""
    agent_id: str
    agent_type: AgentType
    name: str
    description: str
    capabilities: List[AgentCapability]
    model: str
    max_tokens: int
    temperature: float
    timeout: float
    priority: int  # 更高优先级的Agent处理更关键任务
    rate_limit: int  # 每分钟最大请求数
    cost_per_token: float

@dataclass
class AgentPerformance:
    """Agent性能指标。"""
    agent_id: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time: float = 0.0
    avg_quality_score: float = 0.0
    last_request_time: Optional[datetime] = None
    consecutive_failures: int = 0
    user_satisfaction: float = 0.0

@dataclass
class AgentResponse:
    """Agent响应。"""
    agent_id: str
    agent_type: AgentType
    success: bool
    content: str
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time: float = 0.0
    tool_calls: List[Dict[str, Any]] = field(default_factory=dict)
    requires_escalation: bool = False
    quality_score: Optional[float] = None

@dataclass
class OrchestrationRequest:
    """Agent编排请求。"""
    request_id: str
    user_id: str
    conversation_id: str
    message: str
    context: Dict[str, Any]
    intent: Optional[IntentCategory] = None
    urgency: Optional[UrgencyLevel] = None
    required_capabilities: List[AgentCapability] = field(default_factory=list)
    preferred_agents: List[str] = field(default_factory=list)
    max_agents: int = 3
    timeout: float = 30.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class OrchestrationResult:
    """Agent编排结果。"""
    request_id: str
    success: bool
    primary_response: Optional[AgentResponse] = None
    secondary_responses: List[AgentResponse] = field(default_factory=list)
    execution_plan: List[Dict[str, Any]] = field(default_factory=list)
    total_execution_time: float = 0.0
    escalation_triggered: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

class Agent:
    """具有专用能力的基础Agent类。"""

    def __init__(
        self,
        config: AgentConfig,
        anthropic_client: AsyncAnthropic
    ):
        self.config = config
        self.anthropic = anthropic_client
        self.status = AgentStatus.AVAILABLE
        self.performance = AgentPerformance(agent_id=config.agent_id)
        self.current_requests = 0
        self.lock = asyncio.Lock()
        self.knowledge_base: Dict[str, Any] = {}

    async def process(
        self,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """处理请求并返回响应。"""
        start_time = time.time()

        try:
            # 更新性能跟踪
            self.performance.total_requests += 1
            self.performance.last_request_time = datetime.now()

            # 执行Agent特定逻辑
            response = await self._execute(request, context)

            # 更新性能指标
            execution_time = time.time() - start_time
            response.execution_time = execution_time

            if response.success:
                self.performance.successful_requests += 1
                self.performance.consecutive_failures = 0
            else:
                self.performance.failed_requests += 1
                self.performance.consecutive_failures += 1

            # 更新平均响应时间
            total_time = (self.performance.avg_response_time *
                         (self.performance.total_requests - 1) + execution_time)
            self.performance.avg_response_time = total_time / self.performance.total_requests

            return response

        except Exception as e:
            logger.error(f"Agent {self.config.agent_id} 处理错误: {e}")
            self.performance.failed_requests += 1
            self.performance.consecutive_failures += 1

            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=False,
                content=f"处理请求错误: {str(e)}",
                confidence=0.0,
                execution_time=time.time() - start_time
            )

    async def _execute(
        self,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """执行Agent特定的处理逻辑。"""
        raise NotImplementedError("子类必须实现_execute")

    async def acquire(self) -> bool:
        """尝试获取此Agent进行处理。"""
        async with self.lock:
            if self.status == AgentStatus.AVAILABLE and self.current_requests < self.config.rate_limit:
                self.current_requests += 1
                if self.current_requests >= self.config.rate_limit:
                    self.status = AgentStatus.BUSY
                return True
            return False

    async def release(self):
        """处理后释放Agent。"""
        async with self.lock:
            self.current_requests -= 1
            if self.current_requests == 0 and self.status == AgentStatus.BUSY:
                self.status = AgentStatus.AVAILABLE

    def get_performance_summary(self) -> Dict[str, Any]:
        """获取Agent性能摘要。"""
        success_rate = (
            self.performance.successful_requests / self.performance.total_requests
            if self.performance.total_requests > 0 else 0
        )

        return {
            "agent_id": self.config.agent_id,
            "agent_type": self.config.agent_type.value,
            "status": self.status.value,
            "total_requests": self.performance.total_requests,
            "success_rate": success_rate,
            "avg_response_time": self.performance.avg_response_time,
            "avg_quality_score": self.performance.avg_quality_score,
            "consecutive_failures": self.performance.consecutive_failures,
            "user_satisfaction": self.performance.user_satisfaction
        }

class TechnicalAgent(Agent):
    """技术支持专用Agent。"""

    async def _execute(
        self,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """执行技术支持逻辑。"""
        system_prompt = """你是技术支持专家。帮助用户处理:
- 技术问题和故障排除
- 错误消息和调试
- 系统配置和设置
- 技术文档和指南

要彻底、系统化，并提供逐步解决方案。"""

        user_message = self._format_message(request, context)

        try:
            response = await self.anthropic.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            content = response.content[0].text

            # 分析是否需要升级
            requires_escalation = self._check_escalation_needed(content, request)

            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=True,
                content=content,
                confidence=0.8,
                requires_escalation=requires_escalation,
                metadata={
                    "technical_terms": self._extract_technical_terms(content),
                    "solutions_provided": self._count_solutions(content)
                }
            )

        except Exception as e:
            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=False,
                content=f"技术支持错误: {str(e)}",
                confidence=0.0
            )

    def _format_message(self, request: OrchestrationRequest, context: Dict) -> str:
        """格式化消息和上下文。"""
        parts = [f"用户问题: {request.message}"]

        if context.get('conversation_history'):
            parts.append("\n最近对话:")
            for msg in context['conversation_history'][-3:]:
                parts.append(f"{msg['role']}: {msg['content']}")

        if context.get('user_info'):
            parts.append(f"\n用户上下文: {context['user_info']}")

        return "\n".join(parts)

    def _check_escalation_needed(self, content: str, request: OrchestrationRequest) -> bool:
        """检查问题是否需要升级。"""
        escalation_keywords = ['senior engineer', 'escalate', 'specialist', 'expert', 'complicated']
        return any(keyword in content.lower() for keyword in escalation_keywords)

    def _extract_technical_terms(self, content: str) -> List[str]:
        """从响应中提取技术术语。"""
        # 简化提取
        technical_terms = []
        common_terms = ['API', 'SDK', 'database', 'server', 'client', 'network', 'protocol']
        for term in common_terms:
            if term.lower() in content.lower():
                technical_terms.append(term)
        return technical_terms

    def _count_solutions(self, content: str) -> int:
        """统计提供的解决方案数量。"""
        solution_indicators = ['step', 'solution', 'fix', 'resolve', 'try', 'option']
        return sum(1 for indicator in solution_indicators if indicator in content.lower())

class BillingAgent(Agent):
    """账单和支付查询专用Agent。"""

    async def _execute(
        self,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """执行账单支持逻辑。"""
        system_prompt = """你是账单支持专家。帮助用户处理:
- 计费查询和争议
- 支付处理问题
- 退款和积分
- 发票和账户余额问题
- 订阅和计划变更

要准确、专业，并对财务事项敏感。"""

        user_message = f"用户问题: {request.message}"

        if context.get('billing_info'):
            user_message += f"\n\n账单信息: {context['billing_info']}"

        try:
            response = await self.anthropic.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            content = response.content[0].text

            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=True,
                content=content,
                confidence=0.9,
                metadata={
                    "billing_topic": self._identify_billing_topic(content),
                    "action_required": self._check_action_required(content)
                }
            )

        except Exception as e:
            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=False,
                content=f"账单支持错误: {str(e)}",
                confidence=0.0
            )

    def _identify_billing_topic(self, content: str) -> str:
        """识别账单主题。"""
        topics = ['refund', 'invoice', 'payment', 'subscription', 'credit', 'dispute']
        for topic in topics:
            if topic in content.lower():
                return topic
        return 'general'

    def _check_action_required(self, content: str) -> bool:
        """检查是否需要账单团队采取行动。"""
        action_keywords = ['escalate', 'manual review', 'investigate', 'contact billing']
        return any(keyword in content.lower() for keyword in action_keywords)

class GeneralAgent(Agent):
    """处理各种查询的通用Agent。"""

    async def _execute(
        self,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """执行通用支持逻辑。"""
        system_prompt = """你是一个有用的客服Agent。帮助用户处理:
- 一般咨询
- 产品信息
- 服务问题
- 基础故障排除
- 路由到专业支持

要友好、准确、高效。如果问题需要专业知识，请说明。"""

        user_message = f"用户问题: {request.message}"

        if context.get('conversation_history'):
            user_message += "\n\n提供最近对话上下文。"

        try:
            response = await self.anthropic.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            content = response.content[0].text

            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=True,
                content=content,
                confidence=0.7,
                metadata={
                    "topic": self._identify_topic(content),
                    "complexity": self._assess_complexity(content)
                }
            )

        except Exception as e:
            return AgentResponse(
                agent_id=self.config.agent_id,
                agent_type=self.config.agent_type,
                success=False,
                content=f"通用支持错误: {str(e)}",
                confidence=0.0
            )

    def _identify_topic(self, content: str) -> str:
        """识别主要主题。"""
        # 简单主题识别
        return "general"

    def _assess_complexity(self, content: str) -> str:
        """评估响应复杂度。"""
        if len(content) < 200:
            return "simple"
        elif len(content) < 500:
            return "moderate"
        else:
            return "complex"

class AgentOrchestrator:
    """
    高级多Agent编排系统。

    能力:
    1. 基于意图和上下文的智能Agent路由
    2. 复杂查询的并行Agent执行
    3. 基于性能的Agent选择
    4. 自动升级和降级
    5. 跨Agent协作
    6. 实时性能监控
    """

    def __init__(
        self,
        anthropic_api_key: str,
        intent_recognizer: IntentRecognizer
    ):
        self.anthropic = AsyncAnthropic(api_key=anthropic_api_key)
        self.intent_recognizer = intent_recognizer
        self.agents: Dict[str, Agent] = {}
        self.agent_routing_rules: Dict[IntentCategory, List[str]] = {}
        self.performance_history: Dict[str, List[Dict]] = defaultdict(list)
        self.escalation_rules: Dict[str, Callable] = {}

        # 初始化专用Agent
        self._initialize_agents()
        self._setup_routing_rules()
        self._setup_escalation_rules()

    def _initialize_agents(self):
        """初始化专用Agent。"""
        # 技术Agent
        tech_config = AgentConfig(
            agent_id="tech_agent_1",
            agent_type=AgentType.TECHNICAL,
            name="技术支持Agent",
            description="专用于技术问题和故障排除",
            capabilities=[AgentCapability.TOOL_EXECUTION, AgentCapability.KNOWLEDGE_RETRIEVAL],
            model="claude-3-5-sonnet-20240229",
            max_tokens=4096,
            temperature=0.3,
            timeout=30.0,
            priority=2,
            rate_limit=10,
            cost_per_token=0.0003
        )
        self.agents[tech_config.agent_id] = TechnicalAgent(tech_config, self.anthropic)

        # 账单Agent
        billing_config = AgentConfig(
            agent_id="billing_agent_1",
            agent_type=AgentType.BILLING,
            name="账单支持Agent",
            description="专用于计费和支付查询",
            capabilities=[AgentCapability.MEMORY_ACCESS, AgentCapability.ANALYSIS],
            model="claude-3-5-sonnet-20240229",
            max_tokens=2048,
            temperature=0.2,
            timeout=20.0,
            priority=3,
            rate_limit=15,
            cost_per_token=0.0003
        )
        self.agents[billing_config.agent_id] = BillingAgent(billing_config, self.anthropic)

        # 通用Agent
        general_config = AgentConfig(
            agent_id="general_agent_1",
            agent_type=AgentType.GENERAL,
            name="通用支持Agent",
            description="处理一般咨询和基础支持",
            capabilities=[AgentCapability.INTENT_RECOGNITION, AgentCapability.COORDINATION],
            model="claude-3-5-sonnet-20240229",
            max_tokens=2048,
            temperature=0.5,
            timeout=15.0,
            priority=1,
            rate_limit=20,
            cost_per_token=0.0003
        )
        self.agents[general_config.agent_id] = GeneralAgent(general_config, self.anthropic)

        logger.info(f"已初始化 {len(self.agents)} 个Agent")

    def _setup_routing_rules(self):
        """设置基于意图的Agent路由规则。"""
        self.agent_routing_rules = {
            IntentCategory.TECHNICAL: ["tech_agent_1"],
            IntentCategory.BILLING: ["billing_agent_1"],
            IntentCategory.ACCOUNT: ["general_agent_1"],
            IntentCategory.QUERY: ["general_agent_1"],
            IntentCategory.COMPLAINT: ["general_agent_1"],
            IntentCategory.REQUEST: ["general_agent_1"],
            IntentCategory.GREETING: ["general_agent_1"],
            IntentCategory.ESCALATION: ["general_agent_1"],
            IntentCategory.FEEDBACK: ["general_agent_1"],
            IntentCategory.OTHER: ["general_agent_1"]
        }

    def _setup_escalation_rules(self):
        """设置自动升级规则。"""
        def urgent_escalation(response: AgentResponse, request: OrchestrationRequest) -> bool:
            return (request.urgency == UrgencyLevel.CRITICAL or
                   response.requires_escalation or
                   response.confidence < 0.5)

        def failure_escalation(response: AgentResponse, request: OrchestrationRequest) -> bool:
            return not response.success

        self.escalation_rules = {
            'urgent': urgent_escalation,
            'failure': failure_escalation
        }

    async def orchestrate(
        self,
        request: OrchestrationRequest
    ) -> OrchestrationResult:
        """
        为请求编排Agent执行。

        参数:
            request: 包含所有必要信息的编排请求

        返回:
            包含Agent响应的OrchestrationResult
        """
        start_time = time.time()
        request.request_id = request.request_id or str(uuid.uuid4())

        # 如果未提供则识别意图
        if not request.intent:
            intent_result = await self.intent_recognizer.recognize_intent(
                request.message,
                request.context,
                request.context.get('conversation_history')
            )
            request.intent = intent_result.intent
            request.urgency = intent_result.urgency
            request.metadata['intent_result'] = intent_result

        # 确定执行计划
        execution_plan = await self._create_execution_plan(request)

        # 执行计划
        result = await self._execute_plan(request, execution_plan)

        # 检查升级
        if self._should_escalate(result, request):
            result = await self._handle_escalation(request, result)

        # 计算总执行时间
        result.total_execution_time = time.time() - start_time

        # 记录性能
        await self._record_performance(request, result)

        return result

    async def _create_execution_plan(
        self,
        request: OrchestrationRequest
    ) -> List[Dict[str, Any]]:
        """为请求创建执行计划。"""
        plan = []

        # 根据意图获取路由Agent
        routed_agent_ids = self.agent_routing_rules.get(
            request.intent,
            ["general_agent_1"]
        )

        # 筛选可用Agent
        available_agents = [
            agent_id for agent_id in routed_agent_ids
            if agent_id in self.agents and
            self.agents[agent_id].status == AgentStatus.AVAILABLE
        ]

        if not available_agents:
            # 降级到通用Agent
            available_agents = ["general_agent_1"]

        # 基于性能选择最佳Agent
        selected_agents = self._select_best_agents(available_agents, request)

        # 创建计划步骤
        for i, agent_id in enumerate(selected_agents[:request.max_agents]):
            plan.append({
                'step': i + 1,
                'agent_id': agent_id,
                'agent_type': self.agents[agent_id].config.agent_type,
                'parallel': i > 0,  # 第一个Agent顺序执行，其他并行
                'priority': self.agents[agent_id].config.priority
            })

        return plan

    def _select_best_agents(
        self,
        available_agents: List[str],
        request: OrchestrationRequest
    ) -> List[str]:
        """基于性能和需求选择最佳Agent。"""
        scored_agents = []

        for agent_id in available_agents:
            agent = self.agents[agent_id]
            performance = agent.performance

            # 计算综合得分
            success_rate = (
                performance.successful_requests / performance.total_requests
                if performance.total_requests > 0 else 1.0
            )

            # 偏好具有更高成功率和更低响应时间的Agent
            score = (
                success_rate * 0.6 +
                (1.0 / (1.0 + performance.avg_response_time)) * 0.3 +
                agent.config.priority * 0.1
            )

            scored_agents.append((agent_id, score))

        # 按得分排序（降序）
        scored_agents.sort(key=lambda x: x[1], reverse=True)

        return [agent_id for agent_id, _ in scored_agents]

    async def _execute_plan(
        self,
        request: OrchestrationRequest,
        execution_plan: List[Dict[str, Any]]
    ) -> OrchestrationResult:
        """执行编排计划。"""
        result = OrchestrationResult(
            request_id=request.request_id,
            success=False,
            execution_plan=execution_plan
        )

        if not execution_plan:
            return result

        # 顺序执行第一个（主要）Agent
        primary_step = execution_plan[0]
        primary_agent = self.agents[primary_step['agent_id']]

        if await primary_agent.acquire():
            try:
                primary_response = await primary_agent.process(request, request.context)
                result.primary_response = primary_response
                result.success = primary_response.success
            finally:
                await primary_agent.release()
        else:
            # Agent不可用，尝试降级
            result.primary_response = AgentResponse(
                agent_id=primary_step['agent_id'],
                agent_type=primary_step['agent_type'],
                success=False,
                content="Agent暂时不可用",
                confidence=0.0
            )

        # 如果需要并行执行次要Agent
        if len(execution_plan) > 1:
            parallel_tasks = []
            for step in execution_plan[1:]:
                if step['parallel']:
                    agent = self.agents[step['agent_id']]
                    if await agent.acquire():
                        task = self._execute_secondary_agent(agent, request, request.context)
                        parallel_tasks.append(task)

            if parallel_tasks:
                secondary_responses = await asyncio.gather(*parallel_tasks, return_exceptions=True)
                for response in secondary_responses:
                    if isinstance(response, AgentResponse):
                        result.secondary_responses.append(response)
                    elif isinstance(response, Exception):
                        logger.error(f"次要Agent错误: {response}")

        return result

    async def _execute_secondary_agent(
        self,
        agent: Agent,
        request: OrchestrationRequest,
        context: Dict[str, Any]
    ) -> AgentResponse:
        """执行次要Agent并正确清理。"""
        try:
            return await agent.process(request, context)
        finally:
            await agent.release()

    def _should_escalate(
        self,
        result: OrchestrationResult,
        request: OrchestrationRequest
    ) -> bool:
        """确定是否需要升级。"""
        # 检查升级规则
        if result.primary_response:
            for rule_name, rule_func in self.escalation_rules.items():
                if rule_func(result.primary_response, request):
                    logger.info(f"通过规则触发升级: {rule_name}")
                    return True

        # 检查紧急请求
        if request.urgency == UrgencyLevel.CRITICAL:
            return True

        return False

    async def _handle_escalation(
        self,
        request: OrchestrationRequest,
        result: OrchestrationResult
    ) -> OrchestrationResult:
        """处理升级到人工或更高级Agent。"""
        result.escalation_triggered = True
        result.metadata['escalation_reason'] = '基于规则的自动升级'
        result.metadata['escalation_time'] = datetime.now().isoformat()

        # 在生产环境中，这将:
        # 1. 创建升级工单
        # 2. 通知人工Agent
        # 3. 提供上下文摘要
        # 4. 设置适当的优先级

        logger.warning(f"为请求 {request.request_id} 触发升级")

        return result

    async def _record_performance(
        self,
        request: OrchestrationRequest,
        result: OrchestrationResult
    ):
        """记录性能指标用于分析。"""
        timestamp = datetime.now()

        if result.primary_response:
            perf_data = {
                'timestamp': timestamp,
                'request_id': request.request_id,
                'agent_id': result.primary_response.agent_id,
                'intent': request.intent.value if request.intent else 'unknown',
                'success': result.success,
                'execution_time': result.total_execution_time,
                'escalation_triggered': result.escalation_triggered
            }
            self.performance_history[result.primary_response.agent_id].append(perf_data)

    def get_system_performance(self) -> Dict[str, Any]:
        """获取整体系统性能指标。"""
        performance = {
            'total_agents': len(self.agents),
            'available_agents': sum(
                1 for agent in self.agents.values()
                if agent.status == AgentStatus.AVAILABLE
            ),
            'agent_performance': {},
            'total_requests': 0,
            'avg_success_rate': 0.0
        }

        total_requests = 0
        weighted_success_rate = 0.0

        for agent_id, agent in self.agents.items():
            agent_perf = agent.get_performance_summary()
            performance['agent_performance'][agent_id] = agent_perf

            if agent_perf['total_requests'] > 0:
                total_requests += agent_perf['total_requests']
                weighted_success_rate += (
                    agent_perf['success_rate'] * agent_perf['total_requests']
                )

        performance['total_requests'] = total_requests
        if total_requests > 0:
            performance['avg_success_rate'] = weighted_success_rate / total_requests

        return performance

    def add_agent(self, agent: Agent):
        """向编排器添加新Agent。"""
        self.agents[agent.config.agent_id] = agent
        logger.info(f"已添加Agent: {agent.config.agent_id}")

    def remove_agent(self, agent_id: str) -> bool:
        """从编排器中移除Agent。"""
        if agent_id in self.agents:
            del self.agents[agent_id]
            logger.info(f"已移除Agent: {agent_id}")
            return True
        return False

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """通过ID获取Agent。"""
        return self.agents.get(agent_id)

    async def health_check(self) -> Dict[str, Any]:
        """对所有Agent执行健康检查。"""
        health_status = {
            'healthy': True,
            'agents': {},
            'timestamp': datetime.now().isoformat()
        }

        for agent_id, agent in self.agents.items():
            agent_health = {
                'status': agent.status.value,
                'consecutive_failures': agent.performance.consecutive_failures,
                'healthy': agent.status != AgentStatus.ERROR and
                          agent.performance.consecutive_failures < 5
            }
            health_status['agents'][agent_id] = agent_health

            if not agent_health['healthy']:
                health_status['healthy'] = False

        return health_status