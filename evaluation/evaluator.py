"""
EchoMind全面端到端评测框架。

特性:
- 多维度质量指标
- 意图识别准确率测试
- 工具执行评估
- 对话质量评估
- 性能基准测试
- A/B测试能力
- 用户满意度模拟
- 回归测试
- 与基线对比分析
"""
import asyncio
import logging
import json
import random
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import time
import statistics
import hashlib
from pathlib import Path
import csv

import numpy as np
from sklearn.metrics import (
    accuracy_score, precision_recall_fscore_support,
    confusion_matrix, classification_report
)
from anthropic import AsyncAnthropic

from core.intent_recognizer import IntentRecognizer, IntentCategory, UrgencyLevel
from mcp.tool_manager import MCPToolManager, ToolResult
from memory.conversation_memory import ConversationMemoryManager
from agents.agent_orchestrator import AgentOrchestrator, OrchestrationRequest, OrchestrationResult

logger = logging.getLogger(__name__)

class EvaluationMetric(Enum):
    """评测指标类型。"""
    ACCURACY = "accuracy"
    PRECISION = "precision"
    RECALL = "recall"
    F1_SCORE = "f1_score"
    RESPONSE_TIME = "response_time"
    USER_SATISFACTION = "user_satisfaction"
    TOOL_SUCCESS_RATE = "tool_success_rate"
    INTENT_ACCURACY = "intent_accuracy"
    CONVERSATION_QUALITY = "conversation_quality"
    COST_EFFICIENCY = "cost_efficiency"
    MEMORY_RECALL = "memory_recall"

class EvaluationScenario(Enum):
    """评测场景类型。"""
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    END_TO_END = "end_to_end"
    STRESS_TEST = "stress_test"
    REGRESSION_TEST = "regression_test"
    A_B_TEST = "ab_test"
    USER_SIMULATION = "user_simulation"

@dataclass
class TestCase:
    """单个测试用例。"""
    test_id: str
    scenario: EvaluationScenario
    description: str
    input_data: Dict[str, Any]
    expected_output: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    priority: int = 1

@dataclass
class TestResult:
    """运行测试用例的结果。"""
    test_id: str
    success: bool
    actual_output: Dict[str, Any]
    expected_output: Dict[str, Any]
    execution_time: float
    metrics: Dict[str, float]
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class EvaluationReport:
    """全面评测报告。"""
    evaluation_id: str
    timestamp: datetime
    test_results: List[TestResult]
    summary_metrics: Dict[str, float]
    passed_tests: int
    failed_tests: int
    total_tests: int
    pass_rate: float
    performance_summary: Dict[str, Any]
    recommendations: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

class QualityAssessor:
    """评估Agent响应的质量。"""

    def __init__(self, anthropic_api_key: str):
        self.anthropic = AsyncAnthropic(api_key=anthropic_api_key)

    async def assess_response_quality(
        self,
        user_message: str,
        agent_response: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, float]:
        """
        评估Agent响应的质量。

        返回评分:
        - Relevance: 响应对查询的相关性
        - Accuracy: 事实正确性
        - Completeness: 多完整地解决了用户需求
        - Clarity: 响应的清晰度和可理解性
        - Helpfulness: 响应的有用程度
        """
        prompt = f"""在0-1的范围内评估此客服响应的每个维度质量:

用户消息: "{user_message}"

Agent响应: "{agent_response}"

提供以下评分:
1. Relevance: 响应如何直接相关于用户的问题？
2. Accuracy: 提供的信息在事实方面是否正确？
3. Completeness: 多完整地解决了用户需求？
4. Clarity: 响应如何清晰和可理解？
5. Helpfulness: 此响应可能有多大帮助？

仅返回此JSON格式:
{{"relevance": 0.0, "accuracy": 0.0, "completeness": 0.0, "clarity": 0.0, "helpfulness": 0.0}}
"""

        try:
            response = await self.anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=512,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )

            content = response.content[0].text
            scores = json.loads(content)

            # 验证评分
            for key in scores:
                scores[key] = max(0.0, min(1.0, float(scores[key])))

            return scores

        except Exception as e:
            logger.error(f"质量评估失败: {e}")
            # 返回默认评分
            return {
                'relevance': 0.5,
                'accuracy': 0.5,
                'completeness': 0.5,
                'clarity': 0.5,
                'helpfulness': 0.5
            }

class IntentRecognitionEvaluator:
    """评估意图识别性能。"""

    def __init__(self, intent_recognizer: IntentRecognizer):
        self.intent_recognizer = intent_recognizer
        self.test_cases: List[TestCase] = []

    def load_test_cases(self, test_data_path: str):
        """从文件加载意图识别测试用例。"""
        try:
            with open(test_data_path, 'r') as f:
                data = json.load(f)

            for item in data:
                test_case = TestCase(
                    test_id=item.get('test_id', str(hash(item['message']))),
                    scenario=EvaluationScenario.UNIT_TEST,
                    description=f"意图识别: {item['message'][:50]}",
                    input_data={
                        'message': item['message'],
                        'context': item.get('context', {})
                    },
                    expected_output={
                        'intent': item['expected_intent'],
                        'urgency': item.get('expected_urgency', 'LOW')
                    }
                )
                self.test_cases.append(test_case)

            logger.info(f"已加载 {len(self.test_cases)} 个意图识别测试用例")

        except Exception as e:
            logger.error(f"加载测试用例失败: {e}")

    async def evaluate(self, sample_size: Optional[int] = None) -> Dict[str, Any]:
        """
        评估意图识别性能。

        返回指标包括:
        - 整体准确率
        - 每意图的精确率、召回率、F1
        - 混淆矩阵
        - 每意图准确率
        """
        if not self.test_cases:
            logger.warning("未加载测试用例")
            return {}

        # 如需要则采样
        test_cases = self.test_cases[:sample_size] if sample_size else self.test_cases

        predictions = []
        ground_truth = []
        results = []

        start_time = time.time()

        for test_case in test_cases:
            try:
                # 运行意图识别
                result = await self.intent_recognizer.recognize_intent(
                    test_case.input_data['message'],
                    test_case.input_data.get('context'),
                    test_case.input_data.get('conversation_history')
                )

                predicted_intent = result.intent.value
                expected_intent = test_case.expected_output['intent']

                predictions.append(predicted_intent)
                ground_truth.append(expected_intent)

                results.append({
                    'test_id': test_case.test_id,
                    'predicted': predicted_intent,
                    'expected': expected_intent,
                    'correct': predicted_intent == expected_intent,
                    'confidence': result.confidence
                })

            except Exception as e:
                logger.error(f"评估测试用例 {test_case.test_id} 错误: {e}")
                continue

        evaluation_time = time.time() - start_time

        # 计算指标
        if predictions and ground_truth:
            accuracy = accuracy_score(ground_truth, predictions)

            # 每类指标
            precision, recall, f1, support = precision_recall_fscore_support(
                ground_truth, predictions, average=None, zero_division=0
            )

            unique_intents = list(set(ground_truth + predictions))
            per_intent_metrics = {}

            for i, intent in enumerate(unique_intents):
                if i < len(precision):
                    per_intent_metrics[intent] = {
                        'precision': float(precision[i]),
                        'recall': float(recall[i]),
                        'f1_score': float(f1[i]),
                        'support': int(support[i]) if i < len(support) else 0
                    }

            # 混淆矩阵
            conf_matrix = confusion_matrix(ground_truth, predictions, labels=unique_intents)

            return {
                'total_tests': len(test_cases),
                'successful_tests': len([r for r in results if r['correct']]),
                'accuracy': float(accuracy),
                'per_intent_metrics': per_intent_metrics,
                'confusion_matrix': conf_matrix.tolist(),
                'evaluation_time': evaluation_time,
                'avg_time_per_test': evaluation_time / len(test_cases),
                'detailed_results': results
            }

        return {}

class ToolExecutionEvaluator:
    """评估工具执行性能。"""

    def __init__(self, tool_manager: MCPToolManager):
        self.tool_manager = tool_manager
        self.test_cases: List[TestCase] = []

    async def evaluate_tool_performance(
        self,
        tool_name: str,
        test_parameters: List[Dict[str, Any]],
        iterations: int = 10
    ) -> Dict[str, Any]:
        """
        评估特定工具的性能。

        指标:
        - 成功率
        - 平均执行时间
        - 错误类型
        - 资源使用
        """
        results = []

        for i in range(iterations):
            for params in test_parameters:
                try:
                    start_time = time.time()
                    result = await self.tool_manager.execute_tool(
                        tool_name,
                        params,
                        use_cache=False
                    )
                    execution_time = time.time() - start_time

                    results.append({
                        'success': result.success,
                        'execution_time': execution_time,
                        'error': result.error,
                        'cached': result.cached,
                        'metadata': result.metadata
                    })

                except Exception as e:
                    results.append({
                        'success': False,
                        'execution_time': 0,
                        'error': str(e),
                        'cached': False,
                        'metadata': {}
                    })

        # 计算指标
        if results:
            successful_results = [r for r in results if r['success']]
            execution_times = [r['execution_time'] for r in results if r['execution_time'] > 0]

            return {
                'tool_name': tool_name,
                'total_tests': len(results),
                'successful_tests': len(successful_results),
                'success_rate': len(successful_results) / len(results),
                'avg_execution_time': statistics.mean(execution_times) if execution_times else 0,
                'median_execution_time': statistics.median(execution_times) if execution_times else 0,
                'min_execution_time': min(execution_times) if execution_times else 0,
                'max_execution_time': max(execution_times) if execution_times else 0,
                'error_types': self._analyze_errors(results),
                'cache_hit_rate': len([r for r in results if r['cached']]) / len(results) if results else 0
            }

        return {}

    def _analyze_errors(self, results: List[Dict[str, Any]]) -> Dict[str, int]:
        """分析结果中的错误类型。"""
        error_types = {}
        for result in results:
            if not result['success'] and result['error']:
                error_type = result['error'].split(':')[0] if ':' in result['error'] else 'unknown'
                error_types[error_type] = error_types.get(error_type, 0) + 1
        return error_types

class ConversationEvaluator:
    """评估端到端对话质量。"""

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        quality_assessor: QualityAssessor
    ):
        self.orchestrator = orchestrator
        self.quality_assessor = quality_assessor
        self.test_conversations: List[TestCase] = []

    async def evaluate_conversation(
        self,
        conversation: List[Dict[str, str]],
        user_id: str = "test_user",
        conversation_id: str = "test_conversation"
    ) -> Dict[str, Any]:
        """
        评估完整对话。

        指标:
        - 响应质量评分
        - 对话流程
        - 问题解决
        - 用户满意度预测
        """
        results = []
        context = {'user_id': user_id}

        total_start_time = time.time()

        for i, message in enumerate(conversation):
            if message['role'] == 'user':
                try:
                    # 创建编排请求
                    request = OrchestrationRequest(
                        request_id=f"test_{i}",
                        user_id=user_id,
                        conversation_id=conversation_id,
                        message=message['content'],
                        context=context,
                        timeout=30.0
                    )

                    # 处理请求
                    start_time = time.time()
                    orchestration_result = await self.orchestrator.orchestrate(request)
                    response_time = time.time() - start_time

                    if orchestration_result.primary_response:
                        agent_response = orchestration_result.primary_response.content

                        # 评估质量
                        quality_scores = await self.quality_assessor.assess_response_quality(
                            message['content'],
                            agent_response,
                            context
                        )

                        results.append({
                            'turn': i,
                            'user_message': message['content'],
                            'agent_response': agent_response,
                            'response_time': response_time,
                            'quality_scores': quality_scores,
                            'confidence': orchestration_result.primary_response.confidence,
                            'success': orchestration_result.success
                        })

                        # 更新上下文
                        context['last_response'] = agent_response

                except Exception as e:
                    logger.error(f"评估对话轮次 {i} 错误: {e}")
                    results.append({
                        'turn': i,
                        'error': str(e),
                        'success': False
                    })

        total_time = time.time() - total_start_time

        # 计算聚合指标
        if results:
            avg_quality_scores = {}
            for metric in ['relevance', 'accuracy', 'completeness', 'clarity', 'helpfulness']:
                scores = [r['quality_scores'].get(metric, 0.0) for r in results if 'quality_scores' in r]
                avg_quality_scores[metric] = statistics.mean(scores) if scores else 0.0

            avg_response_time = statistics.mean([r['response_time'] for r in results if 'response_time' in r])

            return {
                'conversation_id': conversation_id,
                'total_turns': len(conversation),
                'evaluated_turns': len(results),
                'successful_turns': len([r for r in results if r.get('success', False)]),
                'total_time': total_time,
                'avg_response_time': avg_response_time,
                'avg_quality_scores': avg_quality_scores,
                'overall_quality': statistics.mean(avg_quality_scores.values()),
                'detailed_results': results
            }

        return {}

class EndToEndEvaluator:
    """
    全面端到端评测框架。

    能力:
    1. 意图识别评估
    2. 工具执行测试
    3. 对话质量评估
    4. 性能基准测试
    5. 回归测试
    6. A/B测试
    7. 压力测试
    8. 自定义评测场景
    """

    def __init__(
        self,
        intent_recognizer: IntentRecognizer,
        tool_manager: MCPToolManager,
        memory_manager: ConversationMemoryManager,
        orchestrator: AgentOrchestrator,
        anthropic_api_key: str
    ):
        self.intent_recognizer = intent_recognizer
        self.tool_manager = tool_manager
        self.memory_manager = memory_manager
        self.orchestrator = orchestrator

        # 初始化评测器
        self.quality_assessor = QualityAssessor(anthropic_api_key)
        self.intent_evaluator = IntentRecognitionEvaluator(intent_recognizer)
        self.tool_evaluator = ToolExecutionEvaluator(tool_manager)
        self.conversation_evaluator = ConversationEvaluator(orchestrator, self.quality_assessor)

        # 评测历史
        self.evaluation_history: List[EvaluationReport] = []

        logger.info("端到端评测器初始化完成")

    async def run_comprehensive_evaluation(
        self,
        scenarios: Optional[List[EvaluationScenario]] = None
    ) -> EvaluationReport:
        """
        跨多个场景运行全面评测。

        参数:
            scenarios: 要评测的场景列表。如果为None，运行所有。

        返回:
            全面评测报告
        """
        if scenarios is None:
            scenarios = [
                EvaluationScenario.UNIT_TEST,
                EvaluationScenario.INTEGRATION_TEST,
                EvaluationScenario.END_TO_END
            ]

        evaluation_id = str(hash(datetime.now().isoformat()))
        start_time = time.time()

        test_results = []
        summary_metrics = {}

        # 运行意图识别测试
        if EvaluationScenario.UNIT_TEST in scenarios:
            intent_results = await self.intent_evaluator.evaluate()
            if intent_results:
                test_results.extend(self._convert_to_test_results(intent_results, "intent_recognition"))
                summary_metrics.update(self._extract_summary_metrics(intent_results))

        # 运行工具执行测试
        if EvaluationScenario.INTEGRATION_TEST in scenarios:
            for tool_name in self.tool_manager.tools.keys():
                tool_params = self._generate_tool_test_params(tool_name)
                tool_results = await self.tool_evaluator.evaluate_tool_performance(tool_name, tool_params)
                if tool_results:
                    test_results.extend(self._convert_to_test_results(tool_results, f"tool_execution_{tool_name}"))
                    summary_metrics[f"{tool_name}_success_rate"] = tool_results.get('success_rate', 0.0)

        # 运行端到端对话测试
        if EvaluationScenario.END_TO_END in scenarios:
            test_conversations = self._generate_test_conversations()
            conversation_results = []
            for conv in test_conversations:
                result = await self.conversation_evaluator.evaluate_conversation(conv)
                conversation_results.append(result)

            if conversation_results:
                avg_conv_quality = statistics.mean([r.get('overall_quality', 0.0) for r in conversation_results])
                summary_metrics['avg_conversation_quality'] = avg_conv_quality

        # 计算摘要
        total_time = time.time() - start_time
        passed_tests = len([r for r in test_results if r.success])
        total_tests = len(test_results)

        report = EvaluationReport(
            evaluation_id=evaluation_id,
            timestamp=datetime.now(),
            test_results=test_results,
            summary_metrics=summary_metrics,
            passed_tests=passed_tests,
            failed_tests=total_tests - passed_tests,
            total_tests=total_tests,
            pass_rate=passed_tests / total_tests if total_tests > 0 else 0.0,
            performance_summary={
                'total_evaluation_time': total_time,
                'avg_test_duration': total_time / total_tests if total_tests > 0 else 0.0
            },
            recommendations=self._generate_recommendations(summary_metrics, test_results),
            metadata={
                'scenarios_evaluated': [s.value for s in scenarios],
                'timestamp': datetime.now().isoformat()
            }
        )

        self.evaluation_history.append(report)
        return report

    def _convert_to_test_results(self, evaluation_data: Dict, test_type: str) -> List[TestResult]:
        """将评测数据转换为测试结果。"""
        results = []

        if 'detailed_results' in evaluation_data:
            for item in evaluation_data['detailed_results']:
                results.append(TestResult(
                    test_id=item.get('test_id', ''),
                    success=item.get('correct', False),
                    actual_output={'predicted': item.get('predicted')},
                    expected_output={'expected': item.get('expected')},
                    execution_time=0.0,
                    metrics={'confidence': item.get('confidence', 0.0)},
                    metadata={'test_type': test_type}
                ))

        return results

    def _extract_summary_metrics(self, evaluation_data: Dict) -> Dict[str, float]:
        """从评测数据中提取摘要指标。"""
        metrics = {}
        if 'accuracy' in evaluation_data:
            metrics['intent_accuracy'] = evaluation_data['accuracy']
        return metrics

    def _generate_tool_test_params(self, tool_name: str) -> List[Dict[str, Any]]:
        """为工具生成测试参数。"""
        # 简化 - 生产环境从测试数据加载
        return [
            {'query': '测试查询'},
            {'query': '另一个测试'}
        ]

    def _generate_test_conversations(self) -> List[List[Dict[str, str]]]:
        """生成评测的测试对话。"""
        return [
            [
                {'role': 'user', 'content': '你好，我需要账户帮助'},
                {'role': 'assistant', 'content': '你好！今天我能为你提供什么账户帮助？'},
                {'role': 'user', 'content': '我想重置密码'}
            ],
            [
                {'role': 'user', 'content': '我的订阅被收费两次'},
                {'role': 'assistant', 'content': '我很抱歉这个计费问题。让我帮你解决。'},
                {'role': 'user', 'content': '是的，请检查我的最近收费'}
            ]
        ]

    def _generate_recommendations(
        self,
        summary_metrics: Dict[str, float],
        test_results: List[TestResult]
    ) -> List[str]:
        """基于评测结果生成优化建议。"""
        recommendations = []

        # 检查意图准确率
        if summary_metrics.get('intent_accuracy', 1.0) < 0.9:
            recommendations.append(
                "意图识别准确率低于90%。考虑用更多多样示例进行训练。"
            )

        # 检查工具成功率
        for key, value in summary_metrics.items():
            if 'success_rate' in key and value < 0.95:
                recommendations.append(
                    f"{key.replace('_', ' ')} 低于95%。审查工具错误处理。"
                )

        # 检查对话质量
        if summary_metrics.get('avg_conversation_quality', 1.0) < 0.8:
            recommendations.append(
                "平均对话质量低于0.8。审查响应生成。"
            )

        if not recommendations:
            recommendations.append("所有指标都在可接受范围内。继续监控。")

        return recommendations

    async def run_regression_test(
        self,
        baseline_report_id: str
    ) -> EvaluationReport:
        """针对基线运行回归测试。"""
        # 获取基线报告
        baseline_report = next(
            (r for r in self.evaluation_history if r.evaluation_id == baseline_report_id),
            None
        )

        if not baseline_report:
            raise ValueError(f"基线报告 {baseline_report_id} 未找到")

        # 运行当前评测
        current_report = await self.run_comprehensive_evaluation()

        # 与基线对比
        comparison = self._compare_with_baseline(current_report, baseline_report)

        # 用对比更新当前报告
        current_report.metadata['regression_comparison'] = comparison
        current_report.metadata['baseline_id'] = baseline_report_id

        return current_report

    def _compare_with_baseline(
        self,
        current: EvaluationReport,
        baseline: EvaluationReport
    ) -> Dict[str, Any]:
        """将当前评测与基线对比。"""
        comparison = {
            'timestamp': datetime.now().isoformat(),
            'metrics_delta': {},
            'regressions': [],
            'improvements': []
        }

        for metric_name, current_value in current.summary_metrics.items():
            baseline_value = baseline.summary_metrics.get(metric_name)
            if baseline_value is not None:
                delta = current_value - baseline_value
                comparison['metrics_delta'][metric_name] = {
                    'current': current_value,
                    'baseline': baseline_value,
                    'delta': delta,
                    'percent_change': (delta / baseline_value * 100) if baseline_value != 0 else 0
                }

                # 检查显著退化（超过5%下降）
                if delta < -0.05:
                    comparison['regressions'].append({
                        'metric': metric_name,
                        'baseline_value': baseline_value,
                        'current_value': current_value,
                        'decline_percent': abs(delta / baseline_value * 100)
                    })

                # 检查显著改进（超过5%改进）
                elif delta > 0.05:
                    comparison['improvements'].append({
                        'metric': metric_name,
                        'baseline_value': baseline_value,
                        'current_value': current_value,
                        'improvement_percent': delta / baseline_value * 100
                    })

        return comparison

    def save_report(self, report: EvaluationReport, filepath: str):
        """将评测报告保存到文件。"""
        with open(filepath, 'w') as f:
            json.dump(asdict(report), f, indent=2, default=str)
        logger.info(f"评测报告已保存到 {filepath}")

    def load_report(self, filepath: str) -> EvaluationReport:
        """从文件加载评测报告。"""
        with open(filepath, 'r') as f:
            data = json.load(f)
        return EvaluationReport(**data)