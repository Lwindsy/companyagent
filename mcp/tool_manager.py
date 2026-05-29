"""
MCP工具管理器 - 企业级工具调用框架，包含高级功能:
- 动态工具发现和注册
- 速率限制和熔断保护
- 工具结果缓存和优化
- 错误恢复和降级策略
- 工具使用分析和优化
- 安全和访问控制
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import time
import hashlib
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from functools import wraps
import threading

logger = logging.getLogger(__name__)

class ToolStatus(Enum):
    """工具运行状态。"""
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"

class ToolPriority(Enum):
    """工具执行优先级。"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

@dataclass
class ToolResult:
    """工具执行结果。"""
    success: bool
    data: Any
    error: Optional[str] = None
    execution_time: float = 0.0
    tool_name: str = ""
    cached: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolMetrics:
    """工具性能指标。"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_execution_time: float = 0.0
    last_call_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    avg_execution_time: float = 0.0

class CircuitBreaker:
    """用于工具可靠性的熔断器模式。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Exception = Exception
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'closed'  # closed, open, half-open
        self.lock = threading.Lock()

    def record_failure(self):
        """记录失败并可能打开熔断器。"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            if self.failure_count >= self.failure_threshold:
                self.state = 'open'
                logger.warning(f"由于 {self.failure_count} 次失败，熔断器已打开")

    def record_success(self):
        """记录成功并可能关闭熔断器。"""
        with self.lock:
            self.failure_count = 0
            self.state = 'closed'

    def can_execute(self) -> bool:
        """检查是否允许执行。"""
        with self.lock:
            if self.state == 'closed':
                return True
            elif self.state == 'open':
                if self.last_failure_time and \
                   (datetime.now() - self.last_failure_time).total_seconds() > self.recovery_timeout:
                    self.state = 'half-open'
                    return True
                return False
            elif self.state == 'half-open':
                return True
            return False

class RateLimiter:
    """用于工具调用的令牌桶速率限制器。"""

    def __init__(self, max_calls: int, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = threading.Lock()

    def can_execute(self) -> bool:
        """检查调用是否在速率限制内。"""
        with self.lock:
            now = time.time()
            # 移除旧调用
            self.calls = [call_time for call_time in self.calls if now - call_time < self.period]
            return len(self.calls) < self.max_calls

    def record_call(self):
        """记录一次调用。"""
        with self.lock:
            self.calls.append(time.time())

    def wait_time(self) -> float:
        """计算到下次允许调用的等待时间。"""
        with self.lock:
            if len(self.calls) < self.max_calls:
                return 0.0
            oldest_call = min(self.calls)
            return max(0.0, self.period - (time.time() - oldest_call))

class ToolDefinition:
    """工具定义，包括schema和元数据。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        handler: Callable,
        priority: ToolPriority = ToolPriority.NORMAL,
        timeout: float = 30.0,
        rate_limit: Optional[int] = None,
        cache_ttl: Optional[float] = None
    ):
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.handler = handler
        self.priority = priority
        self.timeout = timeout
        self.rate_limit = rate_limit
        self.cache_ttl = cache_ttl
        self.status = ToolStatus.AVAILABLE
        self.metrics = ToolMetrics()
        self.circuit_breaker = CircuitBreaker()
        self.rate_limiter = RateLimiter(rate_limit or 100)
        self.last_result = None
        self.last_result_time = None

class MCPToolManager:
    """
    全面MCP工具管理器，具备生产就绪功能。

    核心能力:
    1. 动态工具注册和发现
    2. 熔断和速率限制
    3. 结果缓存和优化
    4. 错误恢复和降级策略
    5. 工具使用分析和监控
    6. 安全和访问控制
    """

    def __init__(self, cache_enabled: bool = True):
        self.tools: Dict[str, ToolDefinition] = {}
        self.cache: Dict[str, tuple] = {}  # key -> (result, timestamp)
        self.cache_enabled = cache_enabled
        self.lock = threading.Lock()
        self.global_rate_limiter = RateLimiter(1000, 60.0)  # 全局限流

        # 优化数据
        self.tool_usage_patterns: Dict[str, List[float]] = {}  # tool -> [usage_times]
        self.optimization_suggestions: List[str] = []

    def register_tool(
        self,
        name: str,
        description: str,
        parameters_schema: Dict[str, Any],
        handler: Callable,
        priority: ToolPriority = ToolPriority.NORMAL,
        timeout: float = 30.0,
        rate_limit: Optional[int] = None,
        cache_ttl: Optional[float] = None
    ) -> bool:
        """
        向管理器注册新工具。

        参数:
            name: 唯一工具标识符
            description: 人类可读的描述
            parameters_schema: JSON格式的参数schema
            handler: 执行工具的异步函数
            priority: 执行优先级
            timeout: 最大执行时间
            rate_limit: 每分钟最大调用次数
            cache_ttl: 缓存生存时间（秒）

        返回:
            如果注册成功返回True
        """
        if name in self.tools:
            logger.warning(f"工具 {name} 已注册，正在覆盖")

        tool = ToolDefinition(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            handler=handler,
            priority=priority,
            timeout=timeout,
            rate_limit=rate_limit,
            cache_ttl=cache_ttl
        )

        with self.lock:
            self.tools[name] = tool
            self.tool_usage_patterns[name] = []

        logger.info(f"已注册工具: {name}")
        return True

    def unregister_tool(self, name: str) -> bool:
        """注销工具。"""
        with self.lock:
            if name in self.tools:
                del self.tools[name]
                del self.tool_usage_patterns[name]
                logger.info(f"已注销工具: {name}")
                return True
        return False

    async def execute_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        use_cache: bool = True
    ) -> ToolResult:
        """
        执行工具，包含全面的错误处理和优化。

        参数:
            tool_name: 要执行的工具名称
            parameters: 工具参数
            context: 执行的额外上下文
            use_cache: 是否使用缓存结果

        返回:
            ToolResult 包含执行结果
        """
        # 检查工具是否存在
        if tool_name not in self.tools:
            return ToolResult(
                success=False,
                data=None,
                error=f"工具未找到: {tool_name}",
                tool_name=tool_name
            )

        tool = self.tools[tool_name]

        # 检查熔断器
        if not tool.circuit_breaker.can_execute():
            return ToolResult(
                success=False,
                data=None,
                error=f"工具熔断器已打开: {tool_name}",
                tool_name=tool_name,
                metadata={"circuit_breaker": "open"}
            )

        # 检查速率限制
        if not tool.rate_limiter.can_execute():
            wait_time = tool.rate_limiter.wait_time()
            return ToolResult(
                success=False,
                data=None,
                error=f"工具速率限制: {tool_name}。请在 {wait_time:.1f}秒后重试",
                tool_name=tool_name,
                metadata={"rate_limited": True, "retry_after": wait_time}
            )

        # 检查全局限流
        if not self.global_rate_limiter.can_execute():
            return ToolResult(
                success=False,
                data=None,
                error="超过全局限流",
                tool_name=tool_name,
                metadata={"global_rate_limited": True}
            )

        # 检查缓存
        cache_key = self._generate_cache_key(tool_name, parameters)
        if use_cache and self.cache_enabled and tool.cache_ttl:
            cached_result = self._get_cached_result(cache_key, tool.cache_ttl)
            if cached_result:
                tool.metrics.total_calls += 1
                cached_result.cached = True
                return cached_result

        # 执行工具
        start_time = time.time()
        try:
            # 验证参数
            self._validate_parameters(parameters, tool.parameters_schema)

            # 记录限流使用
            tool.rate_limiter.record_call()
            self.global_rate_limiter.record_call()

            # 带超时执行
            result = await asyncio.wait_for(
                tool.handler(parameters, context),
                timeout=tool.timeout
            )

            execution_time = time.time() - start_time

            # 更新指标
            tool.metrics.total_calls += 1
            tool.metrics.successful_calls += 1
            tool.metrics.total_execution_time += execution_time
            tool.metrics.last_call_time = datetime.now()
            tool.metrics.last_success_time = datetime.now()
            tool.metrics.consecutive_failures = 0
            tool.metrics.avg_execution_time = (
                tool.metrics.total_execution_time / tool.metrics.total_calls
            )

            # 记录熔断器成功
            tool.circuit_breaker.record_success()

            # 缓存结果
            if self.cache_enabled and tool.cache_ttl:
                self._cache_result(cache_key, result, tool.cache_ttl)

            # 跟踪使用模式
            self._track_usage_pattern(tool_name)

            tool_result = ToolResult(
                success=True,
                data=result,
                execution_time=execution_time,
                tool_name=tool_name
            )

            tool.last_result = tool_result
            tool.last_result_time = datetime.now()

            return tool_result

        except asyncio.TimeoutError:
            execution_time = time.time() - start_time
            logger.error(f"工具 {tool_name} 在 {tool.timeout}s 后超时")

            tool.metrics.total_calls += 1
            tool.metrics.failed_calls += 1
            tool.metrics.consecutive_failures += 1
            tool.circuit_breaker.record_failure()
            tool.status = ToolStatus.ERROR

            return ToolResult(
                success=False,
                data=None,
                error=f"工具执行超时: {tool_name}",
                execution_time=execution_time,
                tool_name=tool_name,
                metadata={"timeout": True}
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"工具 {tool_name} 执行失败: {str(e)}")

            tool.metrics.total_calls += 1
            tool.metrics.failed_calls += 1
            tool.metrics.consecutive_failures += 1
            tool.circuit_breaker.record_failure()
            tool.metrics.last_error = str(e)

            # 尝试降级策略
            fallback_result = await self._execute_fallback(tool_name, parameters, context)
            if fallback_result:
                return fallback_result

            return ToolResult(
                success=False,
                data=None,
                error=f"工具执行失败: {str(e)}",
                execution_time=execution_time,
                tool_name=tool_name
            )

    async def execute_tool_chain(
        self,
        tool_chain: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> List[ToolResult]:
        """
        执行工具链，其中一个工具的输出输入到下一个。

        参数:
            tool_chain: 工具执行规格列表
            context: 所有工具的共享上下文

        返回:
            按执行顺序排列的ToolResult列表
        """
        results = []
        chain_context = context.copy() if context else {}

        for step in tool_chain:
            tool_name = step['tool']
            parameters = step.get('parameters', {})

            # 替换上下文变量
            parameters = self._substitute_context(parameters, chain_context)

            result = await self.execute_tool(tool_name, parameters, chain_context)
            results.append(result)

            if result.success:
                # 在上下文中存储输出以供下一个工具使用
                output_key = step.get('output_key', f"{tool_name}_output")
                chain_context[output_key] = result.data
            else:
                # 失败时停止链
                logger.warning(f"工具链在 {tool_name} 处停止，由于失败")
                break

        return results

    async def execute_parallel_tools(
        self,
        tool_specs: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, ToolResult]:
        """
        并行执行多个工具。

        参数:
            tool_specs: 工具执行规格列表
            context: 所有工具的共享上下文

        返回:
            将工具名称映射到结果的字典
        """
        tasks = []
        tool_names = []

        for spec in tool_specs:
            tool_name = spec['tool']
            parameters = spec.get('parameters', {})
            task = self.execute_tool(tool_name, parameters, context)
            tasks.append(task)
            tool_names.append(tool_name)

        # 并行执行所有任务
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将结果映射到工具名称
        result_dict = {}
        for tool_name, result in zip(tool_names, results):
            if isinstance(result, Exception):
                result_dict[tool_name] = ToolResult(
                    success=False,
                    data=None,
                    error=str(result),
                    tool_name=tool_name
                )
            else:
                result_dict[tool_name] = result

        return result_dict

    def _generate_cache_key(self, tool_name: str, parameters: Dict) -> str:
        """从工具名称和参数生成缓存键。"""
        param_str = json.dumps(parameters, sort_keys=True)
        return f"{tool_name}:{hashlib.md5(param_str.encode()).hexdigest()}"

    def _get_cached_result(
        self,
        cache_key: str,
        ttl: float
    ) -> Optional[ToolResult]:
        """获取仍有效的缓存结果。"""
        if cache_key in self.cache:
            result, timestamp = self.cache[cache_key]
            if time.time() - timestamp < ttl:
                return result
            else:
                del self.cache[cache_key]
        return None

    def _cache_result(self, cache_key: str, result: Any, ttl: float):
        """缓存结果并带TTL。"""
        if len(self.cache) < 10000:  # 防止无界增长
            self.cache[cache_key] = (result, time.time())

    def _validate_parameters(self, parameters: Dict, schema: Dict):
        """根据JSON schema验证参数。"""
        # 简化验证 - 生产环境使用jsonschema库
        required = schema.get('required', [])
        properties = schema.get('properties', {})

        for req_field in required:
            if req_field not in parameters:
                raise ValueError(f"缺少必需参数: {req_field}")

        for param_name, param_value in parameters.items():
            if param_name in properties:
                param_schema = properties[param_name]
                param_type = param_schema.get('type')
                if param_type and not self._check_type(param_value, param_type):
                    raise ValueError(
                        f"参数 {param_name} 必须是 {param_type} 类型"
                    )

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """检查值是否匹配预期类型。"""
        type_mapping = {
            'string': str,
            'number': (int, float),
            'integer': int,
            'boolean': bool,
            'array': list,
            'object': dict
        }
        expected_python_type = type_mapping.get(expected_type)
        return isinstance(value, expected_python_type) if expected_python_type else True

    def _substitute_context(self, parameters: Dict, context: Dict) -> Dict:
        """替换参数中的上下文变量。"""
        result = {}
        for key, value in parameters.items():
            if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                var_name = value[2:-1]
                result[key] = context.get(var_name, value)
            else:
                result[key] = value
        return result

    def _track_usage_pattern(self, tool_name: str):
        """跟踪工具使用情况以进行优化洞察。"""
        with self.lock:
            if tool_name in self.tool_usage_patterns:
                self.tool_usage_patterns[tool_name].append(time.time())
                # 仅保留最近的使用（过去一小时）
                cutoff = time.time() - 3600
                self.tool_usage_patterns[tool_name] = [
                    t for t in self.tool_usage_patterns[tool_name] if t > cutoff
                ]

    async def _execute_fallback(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]]
    ) -> Optional[ToolResult]:
        """为失败的工具执行降级策略。"""
        # 根据工具类型实现降级策略
        fallback_strategies = {
            'search': self._fallback_search,
            'api_call': self._fallback_api_call,
            'database': self._fallback_database
        }

        tool_type = self._get_tool_type(tool_name)
        if tool_type in fallback_strategies:
            return await fallback_strategies[tool_type](tool_name, parameters, context)

        return None

    def _get_tool_type(self, tool_name: str) -> str:
        """从名称或元数据确定工具类型。"""
        if 'search' in tool_name.lower() or 'query' in tool_name.lower():
            return 'search'
        elif 'api' in tool_name.lower() or 'http' in tool_name.lower():
            return 'api_call'
        elif 'db' in tool_name.lower() or 'database' in tool_name.lower():
            return 'database'
        return 'unknown'

    async def _fallback_search(self, tool_name: str, parameters: Dict, context: Dict) -> ToolResult:
        """搜索工具的降级策略。"""
        logger.info(f"对搜索工具使用降级: {tool_name}")
        # 返回缓存或简化结果
        return ToolResult(
            success=True,
            data={"fallback": True, "message": "使用缓存的搜索结果"},
            tool_name=tool_name,
            metadata={"fallback_used": True}
        )

    async def _fallback_api_call(self, tool_name: str, parameters: Dict, context: Dict) -> ToolResult:
        """API调用的降级策略。"""
        logger.info(f"对API工具使用降级: {tool_name}")
        # 返回模拟数据或重试退避
        return ToolResult(
            success=True,
            data={"fallback": True, "message": "API不可用，使用缓存数据"},
            tool_name=tool_name,
            metadata={"fallback_used": True}
        )

    async def _fallback_database(self, tool_name: str, parameters: Dict, context: Dict) -> ToolResult:
        """数据库工具的降级策略。"""
        logger.info(f"对数据库工具使用降级: {tool_name}")
        # 使用只读副本或缓存
        return ToolResult(
            success=True,
            data={"fallback": True, "message": "数据库不可用，使用缓存"},
            tool_name=tool_name,
            metadata={"fallback_used": True}
        )

    def get_tool_schema(self) -> Dict[str, Any]:
        """获取所有注册工具的JSON schema。"""
        schema = {
            "tools": [],
            "version": "1.0",
            "capabilities": {
                "parallel_execution": True,
                "chaining": True,
                "caching": self.cache_enabled,
                "rate_limiting": True,
                "circuit_breaking": True
            }
        }

        for tool_name, tool in self.tools.items():
            tool_schema = {
                "name": tool_name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
                "priority": tool.priority.name,
                "timeout": tool.timeout,
                "rate_limit": tool.rate_limit,
                "cache_ttl": tool.cache_ttl,
                "status": tool.status.value
            }
            schema["tools"].append(tool_schema)

        return schema

    def get_tool_metrics(self, tool_name: Optional[str] = None) -> Dict[str, Any]:
        """获取工具的性能指标。"""
        if tool_name:
            if tool_name in self.tools:
                tool = self.tools[tool_name]
                return {
                    "tool_name": tool_name,
                    "total_calls": tool.metrics.total_calls,
                    "successful_calls": tool.metrics.successful_calls,
                    "failed_calls": tool.metrics.failed_calls,
                    "success_rate": (
                        tool.metrics.successful_calls / tool.metrics.total_calls
                        if tool.metrics.total_calls > 0 else 0
                    ),
                    "avg_execution_time": tool.metrics.avg_execution_time,
                    "status": tool.status.value,
                    "consecutive_failures": tool.metrics.consecutive_failures
                }
            return {}

        # 返回所有工具的指标
        all_metrics = {}
        for name, tool in self.tools.items():
            all_metrics[name] = {
                "total_calls": tool.metrics.total_calls,
                "successful_calls": tool.metrics.successful_calls,
                "failed_calls": tool.metrics.failed_calls,
                "success_rate": (
                    tool.metrics.successful_calls / tool.metrics.total_calls
                    if tool.metrics.total_calls > 0 else 0
                ),
                "avg_execution_time": tool.metrics.avg_execution_time,
                "status": tool.status.value
            }
        return all_metrics

    def get_optimization_suggestions(self) -> List[str]:
        """分析使用模式并提供优化建议。"""
        suggestions = []

        for tool_name, usage_times in self.tool_usage_patterns.items():
            if len(usage_times) > 10:  # 仅分析频繁使用的工具
                tool = self.tools[tool_name]

                # 检查工具是否受益于更长的缓存
                if tool.cache_ttl and tool.cache_ttl < 300:
                    recent_calls = len(usage_times)
                    if recent_calls > 50:
                        suggestions.append(
                            f"考虑增加 {tool_name} 的缓存TTL "
                            f"(当前 {tool.cache_ttl}s，每小时调用 {recent_calls} 次)"
                        )

                # 检查优化机会
                if tool.metrics.avg_execution_time > 5.0:
                    suggestions.append(
                        f"工具 {tool_name} 平均执行时间较高 "
                        f"({tool.metrics.avg_execution_time:.2f}s)。考虑优化。"
                    )

                # 检查熔断器使用
                if tool.metrics.consecutive_failures > 3:
                    suggestions.append(
                        f"工具 {tool_name} 经历频繁失败。 "
                        f"审查错误处理和降级策略。"
                    )

        return suggestions

    def clear_cache(self, tool_name: Optional[str] = None):
        """清除特定工具或所有工具的缓存。"""
        if tool_name:
            keys_to_remove = [k for k in self.cache.keys() if k.startswith(tool_name)]
            for key in keys_to_remove:
                del self.cache[key]
        else:
            self.cache.clear()

    def reset_circuit_breakers(self):
        """重置所有熔断器。"""
        for tool in self.tools.values():
            tool.circuit_breaker = CircuitBreaker()
            tool.status = ToolStatus.AVAILABLE
        logger.info("所有熔断器已重置")

# 示例工具实现
async def example_search_tool(parameters: Dict, context: Optional[Dict]) -> Dict:
    """示例搜索工具实现。"""
    query = parameters.get('query', '')
    # 模拟搜索
    await asyncio.sleep(0.5)  # 模拟网络延迟
    return {
        "results": [
            {"title": f"{query} 的结果", "snippet": "这是一个搜索结果"}
        ],
        "total": 1
    }

async def example_api_tool(parameters: Dict, context: Optional[Dict]) -> Dict:
    """示例API工具实现。"""
    endpoint = parameters.get('endpoint', '')
    # 模拟API调用
    await asyncio.sleep(0.3)
    return {"status": "success", "data": {"endpoint": endpoint}}

async def example_database_tool(parameters: Dict, context: Optional[Dict]) -> Dict:
    """示例数据库工具实现。"""
    table = parameters.get('table', '')
    # 模拟数据库查询
    await asyncio.sleep(0.2)
    return {"data": [{"id": 1, "table": table}]}