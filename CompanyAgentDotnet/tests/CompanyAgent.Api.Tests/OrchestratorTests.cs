using CompanyAgent.Api.Agents;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Intent;
using CompanyAgent.Api.Monitoring;
using CompanyAgent.Api.Skills;

namespace CompanyAgent.Api.Tests;

public class OrchestratorTests
{
    private static AgentOrchestrator CreateOrchestrator(ScriptedLlm llm)
    {
        var skills = new SkillManager(TestOptions.Create());
        var pool = new AgentPool([new GeneralAgent(llm, skills), new TechnicalAgent(llm, skills), new BillingAgent(llm, skills)]);
        return new AgentOrchestrator(new IntentRecognizer(llm), pool);
    }

    private static AgentRequest Request(string message, IntentCategory intent, double confidence = 0.9,
        UrgencyLevel urgency = UrgencyLevel.Low)
    {
        var result = new IntentResult(intent, confidence, urgency, intent.Name(), EntityExtractor.Extract(message), "", 0,
            new Dictionary<string, double>());
        return AgentRequest.Create(message, "u1", "c1", "", new List<ChatTurn>(), result);
    }

    [Fact]
    public async Task Cross_domain_question_runs_technical_and_billing_agents_in_parallel()
    {
        var llm = new ScriptedLlm("ok");
        var orchestrator = CreateOrchestrator(llm);

        var result = await orchestrator.RunAsync(Request("退款后应用一直崩溃报错 500", IntentCategory.Refund));

        Assert.Equal(AgentType.Billing, result.PrimaryAgent);
        Assert.Contains(AgentType.Technical, result.SupportingAgents);
        Assert.Contains("[billing - 主处理]", result.Response);
        Assert.Contains("[technical - 辅助处理]", result.Response);
    }

    [Fact]
    public async Task Critical_urgency_escalates()
    {
        var result = await CreateOrchestrator(new ScriptedLlm("ok"))
            .RunAsync(Request("紧急，账户被盗", IntentCategory.AccountSecurity, urgency: UrgencyLevel.Critical));

        Assert.True(result.Escalated);
        Assert.Equal(AgentType.Escalation, result.PrimaryAgent);
        // No dedicated escalation agent: the general agent answers.
        Assert.Equal(AgentType.General, result.AgentType);
    }

    [Fact]
    public async Task Low_confidence_other_intent_asks_for_clarification_without_calling_an_agent()
    {
        var llm = new ScriptedLlm("should not be used");
        var result = await CreateOrchestrator(llm).RunAsync(Request("那个东西怎么弄", IntentCategory.Other, confidence: 0.2));

        Assert.Contains("请补充", result.Response);
        Assert.Empty(llm.Prompts);
    }

    [Fact]
    public async Task Agent_answer_mentioning_a_human_handoff_is_flagged()
    {
        var result = await CreateOrchestrator(new ScriptedLlm("这个需要转人工处理"))
            .RunAsync(Request("我的订单到哪了", IntentCategory.OrderStatus));

        Assert.True(result.Escalated);
    }

    [Fact]
    public void Monitor_penalises_slow_or_failing_agents()
    {
        Assert.Equal(0.0, PerformanceMonitor.RoutingPenalty(1.0, 500));
        Assert.Equal(0.4, PerformanceMonitor.RoutingPenalty(0.7, 500), 3);
        Assert.Equal(0.9, PerformanceMonitor.RoutingPenalty(0.0, 20_000), 3);
    }

    [Fact]
    public void Routing_score_drops_with_penalty()
    {
        var stats = new AgentStats();
        stats.Record(true, 100);
        var healthy = stats.RoutingScore;
        stats.MonitorPenalty = 0.5;

        Assert.Equal(healthy * 0.5, stats.RoutingScore, 6);
        stats.MonitorPenalty = 5; // clamped
        Assert.Equal(0.9, stats.MonitorPenalty);
    }
}
