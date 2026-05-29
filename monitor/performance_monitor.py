"""
EchoMind高级性能监控和优化系统。

特性:
- 实时性能指标收集
- 自动异常检测
- 基于性能的优化建议
- 资源使用监控
- 质量保证跟踪
- 告警和通知系统
- 历史趋势分析
- 预测性能建模
"""
import asyncio
import logging
import json
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import time
import threading
from collections import deque, defaultdict
import statistics
from abc import ABC, abstractmethod

import numpy as np
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import httpx

logger = logging.getLogger(__name__)

class MetricType(Enum):
    """要监控的指标类型。"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"

class AlertSeverity(Enum):
    """告警的严重程度级别。"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class OptimizationType(Enum):
    """优化类型。"""
    CACHE_TTL = "cache_ttl"
    RATE_LIMIT = "rate_limit"
    MODEL_SELECTION = "model_selection"
    RESOURCE_ALLOCATION = "resource_allocation"
    QUERY_OPTIMIZATION = "query_optimization"
    AGENT_ROUTING = "agent_routing"

@dataclass
class Metric:
    """单个指标数据点。"""
    name: str
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE

@dataclass
class PerformanceMetrics:
    """聚合的性能指标。"""
    timestamp: datetime
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time: float = 0.0
    p50_response_time: float = 0.0
    p95_response_time: float = 0.0
    p99_response_time: float = 0.0
    error_rate: float = 0.0
    throughput: float = 0.0  # 每秒请求数
    agent_performance: Dict[str, Dict[str, float]] = field(default_factory=dict)
    tool_performance: Dict[str, Dict[str, float]] = field(default_factory=dict)
    memory_usage: Dict[str, float] = field(default_factory=dict)
    cpu_usage: float = 0.0

@dataclass
class Alert:
    """告警通知。"""
    alert_id: str
    severity: AlertSeverity
    title: str
    description: str
    metric_name: str
    current_value: float
    threshold: float
    timestamp: datetime
    resolved: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class OptimizationRecommendation:
    """系统优化建议。"""
    recommendation_id: str
    optimization_type: OptimizationType
    title: str
    description: str
    expected_improvement: str
    priority: int  # 1-10，数值越高越重要
    effort: int  # 1-10，数值越高工作量越大
    confidence: float  # 0-1
    timestamp: datetime
    implemented: bool = False
    result: Optional[str] = None

class MetricCollector(ABC):
    """指标收集器的抽象基类。"""

    @abstractmethod
    async def collect(self) -> List[Metric]:
        """收集指标。"""
        pass

class AgentMetricsCollector(MetricCollector):
    """收集Agent相关指标。"""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def collect(self) -> List[Metric]:
        """收集Agent性能指标。"""
        metrics = []
        performance = self.orchestrator.get_system_performance()

        # 整体指标
        metrics.append(Metric(
            name="total_agents",
            value=performance['total_agents'],
            timestamp=datetime.now(),
            metric_type=MetricType.GAUGE
        ))

        metrics.append(Metric(
            name="available_agents",
            value=performance['available_agents'],
            timestamp=datetime.now(),
            metric_type=MetricType.GAUGE
        ))

        # 每个Agent的指标
        for agent_id, agent_perf in performance['agent_performance'].items():
            metrics.extend([
                Metric(
                    name=f"agent_requests_total",
                    value=agent_perf['total_requests'],
                    timestamp=datetime.now(),
                    labels={"agent_id": agent_id},
                    metric_type=MetricType.COUNTER
                ),
                Metric(
                    name=f"agent_success_rate",
                    value=agent_perf['success_rate'],
                    timestamp=datetime.now(),
                    labels={"agent_id": agent_id},
                    metric_type=MetricType.GAUGE
                ),
                Metric(
                    name=f"agent_avg_response_time",
                    value=agent_perf['avg_response_time'],
                    timestamp=datetime.now(),
                    labels={"agent_id": agent_id},
                    metric_type=MetricType.GAUGE
                )
            ])

        return metrics

class ToolMetricsCollector(MetricCollector):
    """收集工具相关指标。"""

    def __init__(self, tool_manager):
        self.tool_manager = tool_manager

    async def collect(self) -> List[Metric]:
        """收集工具性能指标。"""
        metrics = []
        tool_metrics = self.tool_manager.get_tool_metrics()

        for tool_name, tool_perf in tool_metrics.items():
            metrics.extend([
                Metric(
                    name=f"tool_calls_total",
                    value=tool_perf['total_calls'],
                    timestamp=datetime.now(),
                    labels={"tool_name": tool_name},
                    metric_type=MetricType.COUNTER
                ),
                Metric(
                    name=f"tool_success_rate",
                    value=tool_perf['success_rate'],
                    timestamp=datetime.now(),
                    labels={"tool_name": tool_name},
                    metric_type=MetricType.GAUGE
                ),
                Metric(
                    name=f"tool_avg_execution_time",
                    value=tool_perf['avg_execution_time'],
                    timestamp=datetime.now(),
                    labels={"tool_name": tool_name},
                    metric_type=MetricType.HISTOGRAM
                ),
                Metric(
                    name=f"tool_consecutive_failures",
                    value=tool_perf['consecutive_failures'],
                    timestamp=datetime.now(),
                    labels={"tool_name": tool_name},
                    metric_type=MetricType.GAUGE
                )
            ])

        return metrics

class SystemMetricsCollector(MetricCollector):
    """收集系统级指标。"""

    async def collect(self) -> List[Metric]:
        """收集系统性能指标。"""
        metrics = []

        # CPU使用率（简化）
        metrics.append(Metric(
            name="system_cpu_usage",
            value=self._get_cpu_usage(),
            timestamp=datetime.now(),
            metric_type=MetricType.GAUGE
        ))

        # 内存使用
        memory_info = self._get_memory_info()
        metrics.append(Metric(
            name="system_memory_used",
            value=memory_info['used'],
            timestamp=datetime.now(),
            metric_type=MetricType.GAUGE
        ))

        metrics.append(Metric(
            name="system_memory_available",
            value=memory_info['available'],
            timestamp=datetime.now(),
            metric_type=MetricType.GAUGE
        ))

        return metrics

    def _get_cpu_usage(self) -> float:
        """获取当前CPU使用率。"""
        # 简化 - 生产环境使用psutil
        return 0.0

    def _get_memory_info(self) -> Dict[str, float]:
        """获取内存使用信息。"""
        # 简化 - 生产环境使用psutil
        return {'used': 0.0, 'available': 0.0}

class AnomalyDetector:
    """使用统计方法检测指标中的异常。"""

    def __init__(self, window_size: int = 100, sensitivity: float = 2.0):
        self.window_size = window_size
        self.sensitivity = sensitivity  # 标准差数量
        self.metric_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))

    def add_metric(self, metric: Metric):
        """添加指标到历史记录用于异常检测。"""
        self.metric_history[metric.name].append(metric.value)

    def detect_anomalies(self) -> List[Dict[str, Any]]:
        """检测所有指标中的异常。"""
        anomalies = []

        for metric_name, values in self.metric_history.items():
            if len(values) < self.window_size // 2:
                continue

            try:
                mean = statistics.mean(values)
                stdev = statistics.stdev(values) if len(values) > 1 else 0

                if stdev > 0:
                    current_value = values[-1]
                    z_score = abs((current_value - mean) / stdev)

                    if z_score > self.sensitivity:
                        anomalies.append({
                            'metric_name': metric_name,
                            'current_value': current_value,
                            'expected_value': mean,
                            'z_score': z_score,
                            'severity': 'high' if z_score > self.sensitivity * 1.5 else 'medium'
                        })

            except statistics.StatisticsError:
                continue

        return anomalies

class PerformanceMonitor:
    """
    全面性能监控系统。

    能力:
    1. 从多个源实时收集指标
    2. 异常检测和告警
    3. 性能趋势分析
    4. 资源使用监控
    5. 自动化优化建议
    6. 历史数据存储和分析
    """

    def __init__(
        self,
        prometheus_port: int = 8000,
        alert_webhook_url: Optional[str] = None,
        anomaly_threshold: float = 2.0
    ):
        self.collectors: List[MetricCollector] = []
        self.metrics_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self.alerts: List[Alert] = []
        self.anomaly_detector = AnomalyDetector(sensitivity=anomaly_threshold)
        self.prometheus_port = prometheus_port
        self.alert_webhook_url = alert_webhook_url
        self.optimization_recommendations: List[OptimizationRecommendation] = []

        # Prometheus指标
        self.prometheus_metrics = {}
        self._setup_prometheus_metrics()

        # 告警规则
        self.alert_rules = self._setup_alert_rules()

        # 监控状态
        self.monitoring_active = False
        self.monitoring_task: Optional[asyncio.Task] = None

        logger.info("性能监控器初始化完成")

    def _setup_prometheus_metrics(self):
        """设置Prometheus指标。"""
        self.prometheus_metrics = {
            'requests_total': Counter('requests_total', '总请求数'),
            'request_duration_seconds': Histogram('request_duration_seconds', '请求持续时间'),
            'active_agents': Gauge('active_agents', '活动Agent数量'),
            'tool_calls_total': Counter('tool_calls_total', '总工具调用', ['tool_name']),
            'errors_total': Counter('errors_total', '总错误数', ['error_type']),
            'cache_hits': Counter('cache_hits_total', '缓存命中'),
            'cache_misses': Counter('cache_misses_total', '缓存未命中')
        }

    def _setup_alert_rules(self) -> Dict[str, Dict[str, Any]]:
        """设置监控的告警规则。"""
        return {
            'high_error_rate': {
                'metric': 'error_rate',
                'threshold': 0.05,  # 5%错误率
                'severity': AlertSeverity.ERROR,
                'description': '错误率超过阈值'
            },
            'slow_response_time': {
                'metric': 'p95_response_time',
                'threshold': 5.0,  # 5秒
                'severity': AlertSeverity.WARNING,
                'description': '响应时间超过阈值'
            },
            'low_success_rate': {
                'metric': 'success_rate',
                'threshold': 0.9,  # 90%成功率
                'severity': AlertSeverity.ERROR,
                'description': '成功率低于阈值'
            },
            'high_memory_usage': {
                'metric': 'memory_usage_percent',
                'threshold': 80.0,  # 80%内存
                'severity': AlertSeverity.WARNING,
                'description': '内存使用率高'
            },
            'agent_unavailable': {
                'metric': 'available_agents',
                'threshold': 1,  # 至少1个Agent可用
                'operator': 'less_than',
                'severity': AlertSeverity.CRITICAL,
                'description': '无Agent可用'
            }
        }

    def register_collector(self, collector: MetricCollector):
        """注册指标收集器。"""
        self.collectors.append(collector)
        logger.info(f"已注册收集器: {collector.__class__.__name__}")

    async def start_monitoring(self, interval: float = 5.0):
        """开始持续监控。"""
        if self.monitoring_active:
            logger.warning("监控已激活")
            return

        self.monitoring_active = True

        # 启动Prometheus服务器
        start_http_server(self.prometheus_port)
        logger.info(f"Prometheus指标服务器已在端口 {self.prometheus_port} 启动")

        # 启动监控循环
        self.monitoring_task = asyncio.create_task(self._monitoring_loop(interval))
        logger.info("性能监控已启动")

    async def stop_monitoring(self):
        """停止持续监控。"""
        self.monitoring_active = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        logger.info("性能监控已停止")

    async def _monitoring_loop(self, interval: float):
        """主监控循环。"""
        while self.monitoring_active:
            try:
                # 收集指标
                await self.collect_metrics()

                # 检查异常
                anomalies = self.anomaly_detector.detect_anomalies()
                if anomalies:
                    logger.warning(f"检测到 {len(anomalies)} 个异常")
                    await self._handle_anomalies(anomalies)

                # 检查告警规则
                await self._check_alert_rules()

                # 生成优化建议
                await self._generate_optimization_recommendations()

                await asyncio.sleep(interval)

            except Exception as e:
                logger.error(f"监控循环错误: {e}")
                await asyncio.sleep(interval)

    async def collect_metrics(self) -> PerformanceMetrics:
        """从所有收集器收集指标。"""
        all_metrics = []

        # 从所有注册的收集器收集
        for collector in self.collectors:
            try:
                metrics = await collector.collect()
                all_metrics.extend(metrics)
            except Exception as e:
                logger.error(f"从 {collector.__class__.__name__} 收集错误: {e}")

        # 在历史记录中存储指标
        for metric in all_metrics:
            self.metrics_history[metric.name].append(metric)
            self.anomaly_detector.add_metric(metric)

            # 更新Prometheus指标
            self._update_prometheus_metric(metric)

        # 聚合性能指标
        return self._aggregate_performance_metrics(all_metrics)

    def _update_prometheus_metric(self, metric: Metric):
        """更新Prometheus指标。"""
        try:
            if metric.name == 'requests_total':
                self.prometheus_metrics['requests_total'].inc(metric.value)
            elif 'response_time' in metric.name:
                self.prometheus_metrics['request_duration_seconds'].observe(metric.value)
            elif metric.name == 'available_agents':
                self.prometheus_metrics['active_agents'].set(metric.value)
            elif 'error' in metric.name.lower():
                self.prometheus_metrics['errors_total'].labels(error_type=metric.labels.get('type', 'unknown')).inc(metric.value)
            elif 'cache_hit' in metric.name:
                self.prometheus_metrics['cache_hits'].inc(metric.value)
            elif 'cache_miss' in metric.name:
                self.prometheus_metrics['cache_misses'].inc(metric.value)
        except Exception as e:
            logger.error(f"更新Prometheus指标错误: {e}")

    def _aggregate_performance_metrics(self, metrics: List[Metric]) -> PerformanceMetrics:
        """将单个指标聚合为性能摘要。"""
        perf_metrics = PerformanceMetrics(timestamp=datetime.now())

        # 计算聚合指标
        request_times = []
        for metric in metrics:
            if 'response_time' in metric.name:
                request_times.append(metric.value)
            elif 'total_requests' in metric.name:
                perf_metrics.total_requests += int(metric.value)
            elif 'successful_requests' in metric.name:
                perf_metrics.successful_requests += int(metric.value)
            elif 'failed_requests' in metric.name:
                perf_metrics.failed_requests += int(metric.value)

        # 计算派生指标
        if perf_metrics.total_requests > 0:
            perf_metrics.error_rate = (
                perf_metrics.failed_requests / perf_metrics.total_requests
            )

        if request_times:
            perf_metrics.avg_response_time = statistics.mean(request_times)
            sorted_times = sorted(request_times)
            n = len(sorted_times)
            perf_metrics.p50_response_time = sorted_times[int(n * 0.5)]
            perf_metrics.p95_response_time = sorted_times[int(n * 0.95)]
            perf_metrics.p99_response_time = sorted_times[int(n * 0.99)]

        return perf_metrics

    async def _handle_anomalies(self, anomalies: List[Dict[str, Any]]):
        """处理检测到的异常。"""
        for anomaly in anomalies:
            # 为异常创建告警
            alert = Alert(
                alert_id=str(hash(anomaly['metric_name'] + str(time.time()))),
                severity=AlertSeverity.WARNING if anomaly['severity'] == 'medium' else AlertSeverity.ERROR,
                title=f"在 {anomaly['metric_name']} 中检测到异常",
                description=f"值 {anomaly['current_value']} 偏离预期 {anomaly['expected_value']} (z分数: {anomaly['z_score']:.2f})",
                metric_name=anomaly['metric_name'],
                current_value=anomaly['current_value'],
                threshold=anomaly['expected_value'],
                timestamp=datetime.now()
            )

            self.alerts.append(alert)
            await self._send_alert(alert)

    async def _check_alert_rules(self):
        """对照当前指标检查所有告警规则。"""
        # 获取当前性能指标
        perf_metrics = await self.collect_metrics()

        # 转换为字典便于访问
        metrics_dict = {
            'error_rate': perf_metrics.error_rate,
            'p95_response_time': perf_metrics.p95_response_time,
            'success_rate': (perf_metrics.successful_requests / perf_metrics.total_requests
                           if perf_metrics.total_requests > 0 else 0.0),
            'memory_usage_percent': perf_metrics.memory_usage.get('percent', 0.0),
            'available_agents': 0.0  # 将从Agent指标填充
        }

        # 检查每个规则
        for rule_name, rule in self.alert_rules.items():
            metric_value = metrics_dict.get(rule['metric'], 0.0)
            threshold = rule['threshold']
            operator = rule.get('operator', 'greater_than')

            triggered = False
            if operator == 'greater_than' and metric_value > threshold:
                triggered = True
            elif operator == 'less_than' and metric_value < threshold:
                triggered = True
            elif operator == 'equals' and metric_value == threshold:
                triggered = True

            if triggered:
                alert = Alert(
                    alert_id=str(hash(rule_name + str(time.time()))),
                    severity=rule['severity'],
                    title=f"告警: {rule_name}",
                    description=rule['description'],
                    metric_name=rule['metric'],
                    current_value=metric_value,
                    threshold=threshold,
                    timestamp=datetime.now()
                )

                self.alerts.append(alert)
                await self._send_alert(alert)

    async def _send_alert(self, alert: Alert):
        """发送告警通知。"""
        logger.warning(f"告警: {alert.title} - {alert.description}")

        # 如果配置了webhook则发送
        if self.alert_webhook_url:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        self.alert_webhook_url,
                        json=asdict(alert),
                        timeout=5.0
                    )
            except Exception as e:
                logger.error(f"发送告警失败: {e}")

    async def _generate_optimization_recommendations(self):
        """基于指标生成优化建议。"""
        perf_metrics = await self.collect_metrics()

        # 检查优化机会
        recommendations = []

        # 高响应时间
        if perf_metrics.p95_response_time > 3.0:
            recommendations.append(OptimizationRecommendation(
                recommendation_id=str(hash('response_time' + str(time.time()))),
                optimization_type=OptimizationType.CACHE_TTL,
                title="减少响应时间",
                description=f"P95响应时间为 {perf_metrics.p95_response_time:.2f}s。考虑增加缓存TTL。",
                expected_improvement="响应时间减少20-30%",
                priority=8,
                effort=3,
                confidence=0.8,
                timestamp=datetime.now()
            ))

        # 高错误率
        if perf_metrics.error_rate > 0.03:
            recommendations.append(OptimizationRecommendation(
                recommendation_id=str(hash('error_rate' + str(time.time()))),
                optimization_type=OptimizationType.AGENT_ROUTING,
                title="减少错误率",
                description=f"错误率为 {perf_metrics.error_rate:.2%}。审查Agent路由规则。",
                expected_improvement="错误减少50%",
                priority=9,
                effort=5,
                confidence=0.7,
                timestamp=datetime.now()
            ))

        # 内存优化
        if perf_metrics.memory_usage.get('percent', 0) > 70:
            recommendations.append(OptimizationRecommendation(
                recommendation_id=str(hash('memory' + str(time.time()))),
                optimization_type=OptimizationType.RESOURCE_ALLOCATION,
                title="优化内存使用",
                description=f"内存使用率为 {perf_metrics.memory_usage.get('percent', 0)}%。考虑内存优化。",
                expected_improvement="内存使用减少30%",
                priority=7,
                effort=4,
                confidence=0.6,
                timestamp=datetime.now()
            ))

        # 添加新建议
        for rec in recommendations:
            if rec.recommendation_id not in {r.recommendation_id for r in self.optimization_recommendations}:
                self.optimization_recommendations.append(rec)
                logger.info(f"生成优化建议: {rec.title}")

    def get_performance_summary(self) -> Dict[str, Any]:
        """获取全面性能摘要。"""
        perf_metrics = asyncio.create_task(self.collect_metrics())
        result = asyncio.run(perf_metrics)

        return {
            'timestamp': result.timestamp.isoformat(),
            'requests': {
                'total': result.total_requests,
                'successful': result.successful_requests,
                'failed': result.failed_requests,
                'error_rate': result.error_rate,
                'throughput': result.throughput
            },
            'performance': {
                'avg_response_time': result.avg_response_time,
                'p50_response_time': result.p50_response_time,
                'p95_response_time': result.p95_response_time,
                'p99_response_time': result.p99_response_time
            },
            'system': {
                'cpu_usage': result.cpu_usage,
                'memory_usage': result.memory_usage
            },
            'alerts': {
                'total': len(self.alerts),
                'active': len([a for a in self.alerts if not a.resolved]),
                'recent': [asdict(a) for a in self.alerts[-5:]]
            },
            'recommendations': [
                {
                    'title': r.title,
                    'priority': r.priority,
                    'confidence': r.confidence,
                    'implemented': r.implemented
                }
                for r in self.optimization_recommendations[-10:]
            ]
        }

    def get_metrics_history(self, metric_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取特定指标的历史数据。"""
        if metric_name not in self.metrics_history:
            return []

        history = list(self.metrics_history[metric_name])
        return [
            {
                'timestamp': m.timestamp.isoformat(),
                'value': m.value,
                'labels': m.labels
            }
            for m in history[-limit:]
        ]

    def resolve_alert(self, alert_id: str) -> bool:
        """解决告警。"""
        for alert in self.alerts:
            if alert.alert_id == alert_id:
                alert.resolved = True
                logger.info(f"已解决告警: {alert.title}")
                return True
        return False

    def implement_recommendation(self, recommendation_id: str, result: str) -> bool:
        """将建议标记为已实施。"""
        for rec in self.optimization_recommendations:
            if rec.recommendation_id == recommendation_id:
                rec.implemented = True
                rec.result = result
                logger.info(f"已实施建议: {rec.title}")
                return True
        return False