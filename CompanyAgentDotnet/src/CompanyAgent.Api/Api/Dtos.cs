using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

namespace CompanyAgent.Api.Api;

/// <summary>Chat request. Accepts both <c>conv_id</c> (Python client) and <c>conversation_id</c> (Java client).</summary>
public sealed class ChatRequest
{
    /// <example>我想申请退款，订单号是 #12345</example>
    [Required(AllowEmptyStrings = false)]
    public string Message { get; set; } = "";

    /// <example>u1001</example>
    public string? UserId { get; set; }

    [JsonPropertyName("conv_id")]
    public string? ConvId { get; set; }

    [JsonPropertyName("conversation_id")]
    public string? ConversationIdAlias { get; set; }

    [JsonIgnore]
    public string? ConversationId => !string.IsNullOrWhiteSpace(ConvId) ? ConvId : ConversationIdAlias;

    [JsonIgnore]
    public string UserIdOrDefault => string.IsNullOrWhiteSpace(UserId) ? "anonymous" : UserId;
}

public sealed record ChatResponse(
    [property: JsonPropertyName("conv_id")] string ConversationId,
    string Response,
    string Intent,
    string IntentGroup,
    string AgentType,
    IReadOnlyList<string> AgentTypes,
    string? PrimaryAgent,
    IReadOnlyList<string> SupportingAgents,
    string RoutingReason,
    double RoutingConfidence,
    bool Escalated,
    long LatencyMs,
    bool KnowledgeUsed,
    bool Verified,
    bool Grounded,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Entities,
    double IntentConfidence,
    IReadOnlyDictionary<string, double> IntentSourceScores);

public sealed class AdminLoginRequest
{
    [Required(AllowEmptyStrings = false)]
    public string Username { get; set; } = "";

    [Required(AllowEmptyStrings = false)]
    public string Password { get; set; } = "";
}

public sealed class DocInput
{
    /// <example>退款补充政策</example>
    [Required(AllowEmptyStrings = false)]
    public string Title { get; set; } = "";

    /// <example>大促期间退款审核时间可能延长到 3-5 个工作日。</example>
    [Required(AllowEmptyStrings = false)]
    public string Content { get; set; } = "";
}

public sealed class BatchDocInput
{
    [Required, MinLength(1)]
    public List<DocInput> Documents { get; set; } = [];
}
