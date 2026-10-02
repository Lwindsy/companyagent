namespace CompanyAgent.Api.Intent;

public enum IntentCategory
{
    Query,
    Complaint,
    Request,
    Greeting,
    Escalation,
    Technical,
    Billing,
    Account,
    Feedback,
    OrderStatus,
    Logistics,
    Refund,
    Invoice,
    PaymentIssue,
    AccountSecurity,
    TechnicalLogin,
    TechnicalCrash,
    HumanHandoff,
    Other,
}

public enum UrgencyLevel
{
    Low,
    Medium,
    High,
    Critical,
}

public sealed record IntentResult(
    IntentCategory Intent,
    double Confidence,
    UrgencyLevel Urgency,
    string IntentGroup,
    IReadOnlyDictionary<string, IReadOnlyList<string>> Entities,
    string Reasoning,
    long LatencyMs,
    IReadOnlyDictionary<string, double> SourceScores);

public static class IntentNames
{
    private static readonly Dictionary<IntentCategory, string> ToWire = Enum.GetValues<IntentCategory>()
        .ToDictionary(c => c, ToSnakeCase);

    private static readonly Dictionary<string, IntentCategory> FromWire = ToWire
        .ToDictionary(kv => kv.Value, kv => kv.Key);

    /// <summary>Wire name used by the API and prompts, e.g. <c>TechnicalLogin</c> → <c>technical_login</c>.</summary>
    public static string Name(this IntentCategory intent) => ToWire[intent];

    public static IntentCategory Parse(string? value) =>
        value is not null && FromWire.TryGetValue(value.Trim().ToLowerInvariant(), out var intent) ? intent : IntentCategory.Other;

    public static IEnumerable<string> All => ToWire.Values;

    private static string ToSnakeCase(IntentCategory intent) =>
        System.Text.Json.JsonNamingPolicy.SnakeCaseLower.ConvertName(intent.ToString());
}
