from agents.agent_orchestrator import AgentType, Request
from core.intent_recognizer import IntentCategory, UrgencyLevel


def _req(message, **kwargs):
    return Request(message=message, user_id="u1", conv_id="c1", **kwargs)


async def test_billing_intent_routes_to_billing_agent(orchestrator, fake_llm):
    fake_llm.intent = "refund"
    result = await orchestrator.run(_req("I want a refund for my last order"))
    assert result.agent_type == AgentType.BILLING
    assert result.intent == IntentCategory.REFUND
    assert not result.escalated


async def test_mixed_problem_dispatches_parallel_agents(orchestrator):
    # billing = 意图 0.75 + "charge" 0.18；technical = 关键词上限 0.45 + error_code 实体 0.2，
    # 达到主 Agent 分数的 55%，所以技术 Agent 作为辅助并行参与。
    result = await orchestrator.run(_req(
        "Login failed with error 401 right after the charge",
        intent=IntentCategory.PAYMENT_ISSUE, intent_confidence=0.9, entities={"error_code": ["401"]},
    ))
    assert result.primary_agent == AgentType.BILLING
    assert result.supporting_agents == [AgentType.TECHNICAL]
    assert "[billing - Primary]" in result.response
    assert "[technical - Supporting]" in result.response


async def test_critical_urgency_escalates(orchestrator):
    result = await orchestrator.run(_req(
        "account hacked", intent=IntentCategory.ACCOUNT_SECURITY, urgency=UrgencyLevel.CRITICAL,
    ))
    assert result.primary_agent == AgentType.ESCALATION
    assert result.escalated


async def test_low_confidence_other_asks_for_clarification_without_agent_call(orchestrator, fake_llm):
    result = await orchestrator.run(_req("hmm what", intent=IntentCategory.OTHER, intent_confidence=0.2))
    assert "not yet sure" in result.response
    assert fake_llm.calls == []


async def test_failed_specialist_falls_back_to_general(orchestrator, fake_llm):
    fake_llm.intent = "refund"
    fake_llm.failing_agents.add("billing")
    result = await orchestrator.run(_req("Please refund my order"))
    assert result.agent_type == AgentType.GENERAL
    assert result.response == "Here are the steps to resolve your issue."
    stats = orchestrator.get_stats()
    assert stats["billing_0"]["success_rate"] == 0.0
    assert stats["general_0"]["success_rate"] == 1.0


async def test_trace_tree_links_orchestrator_agent_and_llm(orchestrator, fake_llm, spans):
    fake_llm.intent = "refund"
    await orchestrator.run(_req("I want a refund"))

    by_name = {s.name: s for s in spans.get_finished_spans()}
    run, intent, agent = by_name["orchestrator.run"], by_name["intent.recognize"], by_name["agent.billing"]
    llm_spans = [s for s in spans.get_finished_spans() if s.name.startswith("chat ")]

    assert intent.parent.span_id == run.context.span_id
    assert agent.parent.span_id == run.context.span_id
    components = {s.attributes["companyagent.llm.component"]: s for s in llm_spans}
    assert components["intent"].parent.span_id == intent.context.span_id
    assert components["agent.billing"].parent.span_id == agent.context.span_id
    assert run.attributes["companyagent.route.primary"] == "billing"
    # 整个请求共享同一个 trace_id
    assert len({s.context.trace_id for s in spans.get_finished_spans()}) == 1
