using CompanyAgent.Api.Common;
using CompanyAgent.Api.Intent;

namespace CompanyAgent.Api.Agents;

public enum AgentType
{
    General,
    Technical,
    Billing,
    Escalation,
}

public static class AgentTypeNames
{
    public static string Name(this AgentType type) => type.ToString().ToLowerInvariant();
}

public sealed record AgentRequest(
    string Message,
    string UserId,
    string ConversationId,
    string Context,
    IReadOnlyList<ChatTurn> History,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Entities,
    IntentCategory? Intent,
    string? IntentGroup,
    UrgencyLevel? Urgency,
    double IntentConfidence,
    string RequestId)
{
    public static AgentRequest Create(string message, string userId, string conversationId, string context,
        IReadOnlyList<ChatTurn> history, IntentResult? intent = null) =>
        new(message, userId, conversationId, context, history,
            intent?.Entities ?? new Dictionary<string, IReadOnlyList<string>>(),
            intent?.Intent, intent?.IntentGroup, intent?.Urgency, intent?.Confidence ?? 1.0,
            Guid.NewGuid().ToString("N")[..8]);

    public AgentRequest WithIntent(IntentResult intent) => this with
    {
        Entities = intent.Entities,
        Intent = intent.Intent,
        IntentGroup = intent.IntentGroup,
        Urgency = intent.Urgency,
        IntentConfidence = intent.Confidence,
    };
}

public sealed record AgentResponse(
    AgentType AgentType,
    string Content,
    bool Success,
    double Confidence,
    long LatencyMs,
    bool Escalate);

public sealed record OrchestratorResult(
    string RequestId,
    string Response,
    AgentType AgentType,
    IntentCategory? Intent,
    bool Escalated,
    long LatencyMs,
    IReadOnlyList<AgentType> AgentTypes,
    AgentType PrimaryAgent,
    IReadOnlyList<AgentType> SupportingAgents,
    string RoutingReason,
    double RoutingConfidence);

/// <summary>Lock-free per-agent counters used for performance-aware routing.</summary>
public sealed class AgentStats
{
    private long _total;
    private long _success;
    private long _totalLatencyMs;
    private double _monitorPenalty;

    public void Record(bool ok, long latencyMs)
    {
        Interlocked.Increment(ref _total);
        if (ok) Interlocked.Increment(ref _success);
        Interlocked.Add(ref _totalLatencyMs, Math.Max(0, latencyMs));
    }

    public long Total => Interlocked.Read(ref _total);

    public double SuccessRate => Total == 0 ? 1.0 : (double)Interlocked.Read(ref _success) / Total;

    public double AvgLatencyMs => Total == 0 ? 0.0 : (double)Interlocked.Read(ref _totalLatencyMs) / Total;

    public double MonitorPenalty
    {
        get => Volatile.Read(ref _monitorPenalty);
        set => Volatile.Write(ref _monitorPenalty, Math.Clamp(value, 0.0, 0.9));
    }

    /// <summary>70% success rate, 30% latency, discounted by the penalty the monitor assigns.</summary>
    public double RoutingScore
    {
        get
        {
            var latencyScore = 1.0 / (1.0 + AvgLatencyMs / 1000.0);
            var baseScore = SuccessRate * 0.7 + latencyScore * 0.3;
            return baseScore * Math.Max(0.0, 1.0 - MonitorPenalty);
        }
    }
}
