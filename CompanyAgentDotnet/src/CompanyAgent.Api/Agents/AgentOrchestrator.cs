using System.Diagnostics;
using System.Globalization;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Intent;

namespace CompanyAgent.Api.Agents;

/// <summary>Agents available per type. More than one instance per type lets routing pick the healthiest.</summary>
public sealed class AgentPool(IEnumerable<BaseAgent> agents)
{
    public IReadOnlyDictionary<AgentType, IReadOnlyList<BaseAgent>> ByType { get; } = agents
        .GroupBy(a => a.Type)
        .ToDictionary(g => g.Key, g => (IReadOnlyList<BaseAgent>)g.ToList());
}

/// <summary>
/// Scores each domain agent from the intent, keywords and extracted entities, then runs the primary
/// agent alone or together with strong-enough supporting agents in parallel.
/// </summary>
public sealed class AgentOrchestrator
{
    private static readonly HashSet<IntentCategory> GeneralIntents =
    [
        IntentCategory.Query, IntentCategory.OrderStatus, IntentCategory.Logistics, IntentCategory.Request,
        IntentCategory.Complaint, IntentCategory.Greeting, IntentCategory.Feedback, IntentCategory.Other,
    ];

    private static readonly HashSet<IntentCategory> TechnicalIntents =
        [IntentCategory.Technical, IntentCategory.TechnicalLogin, IntentCategory.TechnicalCrash];

    private static readonly HashSet<IntentCategory> BillingIntents =
    [
        IntentCategory.Billing, IntentCategory.Account, IntentCategory.AccountSecurity,
        IntentCategory.Refund, IntentCategory.Invoice, IntentCategory.PaymentIssue,
    ];

    private static readonly string[] TechnicalKeywords = ["崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401", "验证码"];
    private static readonly string[] BillingKeywords = ["退款", "退货", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice", "多扣"];
    private static readonly string[] GeneralKeywords = ["订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助"];

    private readonly IntentRecognizer _intentRecognizer;
    private readonly IReadOnlyDictionary<AgentType, IReadOnlyList<BaseAgent>> _pool;

    public AgentOrchestrator(IntentRecognizer intentRecognizer, AgentPool pool)
    {
        _intentRecognizer = intentRecognizer;
        _pool = pool.ByType;
    }

    public async Task<OrchestratorResult> RunAsync(AgentRequest request, CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        var req = request;
        if (req.Intent is null)
        {
            req = req.WithIntent(await _intentRecognizer.RecognizeAsync(req.Message, req.History, cancellationToken));
        }

        if (NeedsClarification(req))
        {
            return new OrchestratorResult(req.RequestId,
                "我还不能确定您要处理的是哪类问题。请补充一下是订单物流、退款账单、账户资料，还是技术故障？",
                AgentType.General, req.Intent, false, stopwatch.ElapsedMilliseconds,
                [AgentType.General], AgentType.General, [], "低置信度 OTHER 意图，先澄清用户需求", req.IntentConfidence);
        }

        var decision = RouteDecision(req);
        if (decision.SupportingAgents.Count > 0)
        {
            return await RunParallelAsync(req, decision, stopwatch, cancellationToken);
        }

        var response = await ExecuteAsync(req, decision.PrimaryAgent, cancellationToken);
        var escalated = response.Escalate
                        || req.Urgency == UrgencyLevel.Critical
                        || req.Intent is IntentCategory.Escalation or IntentCategory.HumanHandoff;
        return new OrchestratorResult(req.RequestId, response.Content, response.AgentType, req.Intent, escalated,
            stopwatch.ElapsedMilliseconds, [response.AgentType], decision.PrimaryAgent, [], decision.Reason, decision.Confidence);
    }

    private async Task<OrchestratorResult> RunParallelAsync(AgentRequest req, RoutingDecision decision, Stopwatch stopwatch,
        CancellationToken cancellationToken)
    {
        var targets = new List<AgentType> { decision.PrimaryAgent };
        targets.AddRange(decision.SupportingAgents);
        var responses = await Task.WhenAll(targets.Select(type => ExecuteAsync(req, type, cancellationToken)));

        var parts = responses
            .Where(r => r.Success)
            .Select(r => $"[{r.AgentType.Name()} - {(r.AgentType == decision.PrimaryAgent ? "主处理" : "辅助处理")}]\n{r.Content}")
            .ToList();
        var succeeded = responses.Where(r => r.Success).Select(r => r.AgentType).ToList();
        return new OrchestratorResult(
            req.RequestId,
            parts.Count == 0 ? "抱歉，所有 Agent 均处理失败。" : string.Join("\n\n", parts),
            decision.PrimaryAgent,
            req.Intent,
            responses.Any(r => r.Escalate),
            stopwatch.ElapsedMilliseconds,
            succeeded.Count == 0 ? targets : succeeded,
            decision.PrimaryAgent,
            decision.SupportingAgents,
            decision.Reason,
            decision.Confidence);
    }

    internal RoutingDecision RouteDecision(AgentRequest req)
    {
        if (req.Urgency == UrgencyLevel.Critical)
        {
            return new RoutingDecision(AgentType.Escalation, [], "紧急度为 CRITICAL，触发升级路由", 1.0);
        }
        if (req.Intent is IntentCategory.Escalation or IntentCategory.HumanHandoff)
        {
            return new RoutingDecision(AgentType.Escalation, [], $"意图为 {req.Intent.Value.Name()}，触发升级路由",
                Math.Max(req.IntentConfidence, 0.8));
        }

        var available = DomainScores(req)
            .Where(kv => kv.Key == AgentType.General || _pool.ContainsKey(kv.Key))
            .OrderByDescending(kv => kv.Value)
            .ToList();
        if (available.Count == 0)
        {
            return new RoutingDecision(AgentType.General, [], "无可用专属 Agent，降级到 GeneralAgent", 0.1);
        }

        var (primary, primaryScore) = available[0];
        var supporting = available
            .Skip(1)
            .Where(kv => kv.Key != AgentType.General && kv.Value >= 0.45 && kv.Value >= primaryScore * 0.55)
            .Select(kv => kv.Key)
            .ToList();
        return new RoutingDecision(primary, supporting, RoutingReason(req, available, primary, supporting),
            Round(Math.Min(primaryScore, 1.0)));
    }

    private static Dictionary<AgentType, double> DomainScores(AgentRequest req)
    {
        var msg = req.Message.ToLowerInvariant();
        var scores = new Dictionary<AgentType, double>
        {
            [AgentType.General] = 0.1,
            [AgentType.Technical] = 0.0,
            [AgentType.Billing] = 0.0,
        };
        if (req.Intent is { } intent)
        {
            if (GeneralIntents.Contains(intent)) scores[AgentType.General] += 0.55;
            if (TechnicalIntents.Contains(intent)) scores[AgentType.Technical] += 0.75;
            if (BillingIntents.Contains(intent)) scores[AgentType.Billing] += 0.75;
        }

        scores[AgentType.Technical] += Math.Min(0.45, TechnicalKeywords.Count(msg.Contains) * 0.18);
        scores[AgentType.Billing] += Math.Min(0.45, BillingKeywords.Count(msg.Contains) * 0.18);
        scores[AgentType.General] += Math.Min(0.35, GeneralKeywords.Count(msg.Contains) * 0.12);

        if (HasEntity(req, "error_code")) scores[AgentType.Technical] += 0.2;
        if (HasEntity(req, "amount")) scores[AgentType.Billing] += 0.15;
        if (HasEntity(req, "order_id")) scores[AgentType.General] += 0.1;

        return scores.ToDictionary(kv => kv.Key, kv => Round(kv.Value));
    }

    private static bool HasEntity(AgentRequest req, string name) =>
        req.Entities.TryGetValue(name, out var values) && values.Count > 0;

    private static string RoutingReason(AgentRequest req, List<KeyValuePair<AgentType, double>> scores,
        AgentType primary, List<AgentType> supporting)
    {
        var scoreText = string.Join(", ", scores.Select(kv => $"{kv.Key.Name()}={kv.Value.ToString("0.00", CultureInfo.InvariantCulture)}"));
        var supportText = supporting.Count == 0 ? "none" : string.Join(", ", supporting.Select(a => a.Name()));
        return $"intent={req.Intent?.Name() ?? "unknown"}, group={req.IntentGroup ?? "unknown"}, "
               + $"primary={primary.Name()}, supporting={supportText}, scores=[{scoreText}]";
    }

    private static bool NeedsClarification(AgentRequest req) =>
        req.Intent == IntentCategory.Other && req.Message.Trim().Length > 2 && req.IntentConfidence < 0.5;

    private async Task<AgentResponse> ExecuteAsync(AgentRequest req, AgentType agentType, CancellationToken cancellationToken)
    {
        // Escalation has no dedicated agent: the general agent answers and the result is flagged for handoff.
        var agent = BestAgent(agentType) ?? BestAgent(AgentType.General);
        if (agent is null)
        {
            return new AgentResponse(AgentType.General, "服务暂时不可用，请稍后重试。", false, 0.0, 0, false);
        }
        var response = await agent.HandleAsync(req, cancellationToken);
        if (!response.Success && agentType != AgentType.General && BestAgent(AgentType.General) is { } general)
        {
            return await general.HandleAsync(req, cancellationToken);
        }
        return response;
    }

    private BaseAgent? BestAgent(AgentType type) =>
        _pool.TryGetValue(type, out var agents) ? agents.MaxBy(a => a.Stats.RoutingScore) : null;

    public Dictionary<string, AgentStatsSnapshot> Stats()
    {
        var result = new Dictionary<string, AgentStatsSnapshot>();
        foreach (var (type, agents) in _pool)
        {
            for (var i = 0; i < agents.Count; i++)
            {
                var s = agents[i].Stats;
                result[$"{type.Name()}_{i}"] = new AgentStatsSnapshot(s.Total, Round(s.SuccessRate), Round(s.AvgLatencyMs),
                    Round(s.MonitorPenalty), Round(s.RoutingScore));
            }
        }
        return result;
    }

    public void UpdateRoutingPenalties(IReadOnlyDictionary<string, double> penalties)
    {
        foreach (var (type, agents) in _pool)
        {
            for (var i = 0; i < agents.Count; i++)
            {
                agents[i].Stats.MonitorPenalty = penalties.GetValueOrDefault($"{type.Name()}_{i}");
            }
        }
    }

    private static double Round(double value) => JsonText.Round(value, 3);

    internal sealed record RoutingDecision(AgentType PrimaryAgent, List<AgentType> SupportingAgents, string Reason, double Confidence);
}

public sealed record AgentStatsSnapshot(long Total, double SuccessRate, double AvgMs, double MonitorPenalty, double RoutingScore);
