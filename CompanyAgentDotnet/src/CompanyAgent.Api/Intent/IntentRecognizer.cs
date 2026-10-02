using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Llm;

namespace CompanyAgent.Api.Intent;

/// <summary>
/// Hybrid intent recognition: an LLM few-shot vote (weight 0.7), character n-gram similarity to
/// templates (0.2) and keyword patterns (0.1). A specific pattern hit can refine a generic LLM label.
/// </summary>
public sealed partial class IntentRecognizer
{
    private const double ConfidenceThreshold = 0.5;
    private const int CacheLimit = 1000;

    private static readonly (IntentCategory Intent, string[] Samples)[] Templates =
    [
        (IntentCategory.Query, ["我的订单状态是什么？", "如何重置密码？", "快递什么时候到？"]),
        (IntentCategory.Complaint, ["等了好几个小时！", "服务太差了！", "一直没人处理！"]),
        (IntentCategory.Request, ["帮我取消订单", "我需要修改地址", "请协助退款"]),
        (IntentCategory.Greeting, ["你好", "嗨，有人吗", "早上好"]),
        (IntentCategory.Escalation, ["我要投诉！", "转人工客服", "找你们经理"]),
        (IntentCategory.Technical, ["应用一直崩溃", "无法登录", "出现500错误"]),
        (IntentCategory.Billing, ["为什么扣了两次款？", "申请退款", "发票问题"]),
        (IntentCategory.Account, ["修改邮箱", "注销账户", "更新个人信息"]),
        (IntentCategory.Feedback, ["服务很棒！", "非常满意", "给个好评"]),
        (IntentCategory.OrderStatus, ["我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？"]),
        (IntentCategory.Logistics, ["快递什么时候到？", "物流一直不更新", "配送要多久？"]),
        (IntentCategory.Refund, ["我要申请退款", "退货退款怎么处理？", "退款多久到账？"]),
        (IntentCategory.Invoice, ["帮我开发票", "发票抬头怎么改？", "电子发票在哪里？"]),
        (IntentCategory.PaymentIssue, ["为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱"]),
        (IntentCategory.AccountSecurity, ["账户被盗了", "发现异常登录", "我要重置密码"]),
        (IntentCategory.TechnicalLogin, ["登录一直报401", "验证码收不到", "无法登录账号"]),
        (IntentCategory.TechnicalCrash, ["应用一直崩溃", "页面报500错误", "系统闪退"]),
        (IntentCategory.HumanHandoff, ["转人工客服", "我要找人工", "请升级处理"]),
    ];

    private static readonly (IntentCategory Intent, string[] Keywords)[] SpecificPatterns =
    [
        (IntentCategory.HumanHandoff, ["转人工", "人工客服", "找人工"]),
        (IntentCategory.OrderStatus, ["订单状态", "发货了吗", "处理到哪", "order status"]),
        (IntentCategory.Logistics, ["物流", "快递", "配送", "运单", "delivery", "shipping"]),
        (IntentCategory.Refund, ["退款", "退货", "refund", "return"]),
        (IntentCategory.Invoice, ["发票", "抬头", "税号", "invoice"]),
        (IntentCategory.PaymentIssue, ["重复扣款", "多扣", "支付失败", "扣费", "payment failed"]),
        (IntentCategory.AccountSecurity, ["被盗", "异常登录", "重置密码", "两步验证", "安全"]),
        (IntentCategory.TechnicalLogin, ["无法登录", "登录失败", "401", "验证码"]),
        (IntentCategory.TechnicalCrash, ["崩溃", "闪退", "500", "报错", "crash"]),
    ];

    private static readonly (IntentCategory Intent, string[] Keywords)[] GenericPatterns =
    [
        (IntentCategory.Escalation, ["投诉", "经理", "supervisor"]),
        (IntentCategory.Complaint, ["太差", "糟糕", "horrible", "等了很久"]),
        (IntentCategory.Query, ["?", "？", "怎么", "什么", "status"]),
        (IntentCategory.Request, ["帮我", "需要", "please", "help"]),
        (IntentCategory.Greeting, ["你好", "嗨", "hello", "hi"]),
        (IntentCategory.Billing, ["退款", "扣款", "发票", "refund"]),
        (IntentCategory.Technical, ["崩溃", "报错", "error", "crash"]),
        (IntentCategory.Account, ["密码", "邮箱", "账户", "password"]),
    ];

    private static readonly HashSet<IntentCategory> SpecificIntents =
    [
        IntentCategory.OrderStatus, IntentCategory.Logistics, IntentCategory.Refund, IntentCategory.Invoice,
        IntentCategory.PaymentIssue, IntentCategory.AccountSecurity, IntentCategory.TechnicalLogin,
        IntentCategory.TechnicalCrash, IntentCategory.HumanHandoff,
    ];

    private static readonly HashSet<IntentCategory> GenericIntents =
    [
        IntentCategory.Query, IntentCategory.Billing, IntentCategory.Technical, IntentCategory.Account, IntentCategory.Escalation,
    ];

    private static readonly Dictionary<IntentCategory, IntentCategory> IntentGroups = new()
    {
        [IntentCategory.OrderStatus] = IntentCategory.Query,
        [IntentCategory.Logistics] = IntentCategory.Query,
        [IntentCategory.Refund] = IntentCategory.Billing,
        [IntentCategory.Invoice] = IntentCategory.Billing,
        [IntentCategory.PaymentIssue] = IntentCategory.Billing,
        [IntentCategory.AccountSecurity] = IntentCategory.Account,
        [IntentCategory.TechnicalLogin] = IntentCategory.Technical,
        [IntentCategory.TechnicalCrash] = IntentCategory.Technical,
        [IntentCategory.HumanHandoff] = IntentCategory.Escalation,
    };

    // Template n-grams are computed once instead of on every request.
    private static readonly (IntentCategory Intent, HashSet<string> Grams)[] TemplateGrams = Templates
        .SelectMany(t => t.Samples.Select(sample => (t.Intent, CharNgrams(sample))))
        .ToArray();

    private readonly ILlmGateway _llm;
    private readonly ConcurrentDictionary<string, IntentResult> _cache = new();

    public IntentRecognizer(ILlmGateway llm)
    {
        _llm = llm;
    }

    public async Task<IntentResult> RecognizeAsync(string message, IReadOnlyList<ChatTurn>? history,
        CancellationToken cancellationToken = default)
    {
        var key = CacheKey(message, history);
        if (_cache.TryGetValue(key, out var cached))
        {
            return cached;
        }

        var stopwatch = Stopwatch.StartNew();
        var llm = await LlmRecognizeAsync(message, history, cancellationToken);
        var semantic = SemanticRecognize(message);
        var pattern = PatternRecognize(message);
        var vote = Vote(llm, semantic, pattern);
        var result = new IntentResult(
            vote.Intent,
            vote.Confidence,
            Urgency(message, vote.Intent),
            IntentGroups.GetValueOrDefault(vote.Intent, vote.Intent).Name(),
            EntityExtractor.Extract(message),
            llm.Reasoning,
            stopwatch.ElapsedMilliseconds,
            vote.SourceScores);

        if (_cache.Count > CacheLimit)
        {
            _cache.Clear();
        }
        _cache[key] = result;
        return result;
    }

    private async Task<Signal> LlmRecognizeAsync(string message, IReadOnlyList<ChatTurn>? history, CancellationToken cancellationToken)
    {
        var examples = new StringBuilder();
        foreach (var (intent, samples) in Templates)
        {
            examples.Append("消息: \"").Append(samples[0]).Append("\" -> 意图: ").Append(intent.Name()).Append('\n');
        }
        var historyText = history is null ? "" : string.Join("\n", history.Select(t => $"{t.Role}: {t.Content}"));
        var prompt = $$"""
            你是客服意图分析专家。根据示例判断用户意图，返回 JSON。
            如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
            例如退款优先返回 refund，发票优先返回 invoice，登录故障优先返回 technical_login。

            示例:
            {{examples}}
            最近对话:
            {{historyText}}
            用户消息: "{{message}}"
            返回格式: {"intent":"technical","confidence":0.9,"reasoning":"一句话说明"}
            可选意图: {{string.Join(", ", IntentNames.All)}}
            """;
        try
        {
            var raw = await _llm.ChatAsync("", prompt, 0.1, 256, cancellationToken);
            var json = JsonText.ExtractObject(raw) ?? throw new FormatException("No JSON object in LLM reply");
            return new Signal(
                IntentNames.Parse(json["intent"]?.ToString()),
                JsonText.AsDouble(json["confidence"]) ?? 0.0,
                json["reasoning"]?.ToString() ?? "",
                Failed: false);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return new Signal(IntentCategory.Other, 0.0, "LLM recognition failed", Failed: true);
        }
    }

    private static Signal SemanticRecognize(string message)
    {
        var grams = CharNgrams(message);
        var best = IntentCategory.Other;
        var bestScore = 0.0;
        foreach (var (intent, templateGrams) in TemplateGrams)
        {
            var score = Jaccard(grams, templateGrams);
            if (score > bestScore)
            {
                bestScore = score;
                best = intent;
            }
        }
        return new Signal(best, bestScore);
    }

    private static Signal PatternRecognize(string message)
    {
        var text = Normalize(message);
        var specific = BestPatternMatch(text, SpecificPatterns);
        return specific.Intent != IntentCategory.Other ? specific : BestPatternMatch(text, GenericPatterns);
    }

    private static Signal BestPatternMatch(string text, (IntentCategory Intent, string[] Keywords)[] patterns)
    {
        var best = IntentCategory.Other;
        var bestScore = 0.0;
        foreach (var (intent, keywords) in patterns)
        {
            var hits = keywords.Count(text.Contains);
            if (hits == 0) continue;
            var score = Math.Min(1.0, 0.5 + 0.25 * (hits - 1));
            if (score > bestScore)
            {
                bestScore = score;
                best = intent;
            }
        }
        return new Signal(best, bestScore);
    }

    private static (IntentCategory Intent, double Confidence, Dictionary<string, double> SourceScores) Vote(
        Signal llm, Signal semantic, Signal pattern)
    {
        var sourceScores = new Dictionary<string, double>
        {
            ["llm"] = llm.Confidence,
            ["embedding"] = semantic.Confidence,
            ["pattern"] = pattern.Confidence,
        };

        if (llm.Failed)
        {
            // Without the LLM, trust the n-gram signal first, then keywords.
            if (semantic.Intent != IntentCategory.Other && semantic.Confidence > 0) return (semantic.Intent, semantic.Confidence, sourceScores);
            if (pattern.Intent != IntentCategory.Other && pattern.Confidence > 0) return (pattern.Intent, pattern.Confidence, sourceScores);
            return (IntentCategory.Other, 0.0, sourceScores);
        }

        var scores = new Dictionary<IntentCategory, double>();
        void Add(Signal s, double weight) => scores[s.Intent] = scores.GetValueOrDefault(s.Intent) + weight * s.Confidence;
        Add(llm, 0.70);
        Add(semantic, 0.20);
        Add(pattern, 0.10);
        var best = scores.MaxBy(kv => kv.Value);

        if (GenericIntents.Contains(best.Key) && SpecificIntents.Contains(pattern.Intent)
            && pattern.Confidence >= 0.5 && best.Value < 0.8)
        {
            sourceScores["refined_by_pattern"] = pattern.Confidence;
            return (pattern.Intent, Math.Max(best.Value, pattern.Confidence), sourceScores);
        }
        return best.Value < ConfidenceThreshold
            ? (IntentCategory.Other, best.Value, sourceScores)
            : (best.Key, best.Value, sourceScores);
    }

    private static UrgencyLevel Urgency(string message, IntentCategory intent)
    {
        var text = Normalize(message);
        if (text.Contains("紧急") || text.Contains("urgent") || text.Contains("立刻")) return UrgencyLevel.Critical;
        if (text.Contains("今天") || text.Contains("马上") || text.Contains("尽快")
            || intent is IntentCategory.Escalation or IntentCategory.HumanHandoff) return UrgencyLevel.High;
        return intent == IntentCategory.Complaint ? UrgencyLevel.Medium : UrgencyLevel.Low;
    }

    internal static HashSet<string> CharNgrams(string? text)
    {
        var normalized = Normalize(text);
        var grams = new HashSet<string>();
        for (var n = 1; n <= 3; n++)
        {
            for (var i = 0; i + n <= normalized.Length; i++)
            {
                grams.Add(normalized.Substring(i, n));
            }
        }
        return grams;
    }

    private static double Jaccard(HashSet<string> left, HashSet<string> right)
    {
        if (left.Count == 0 || right.Count == 0) return 0.0;
        var intersection = left.Count(right.Contains);
        var union = left.Count + right.Count - intersection;
        return union == 0 ? 0.0 : (double)intersection / union;
    }

    private static string Normalize(string? value) => (value ?? "").ToLowerInvariant().Trim();

    private static string CacheKey(string message, IReadOnlyList<ChatTurn>? history)
    {
        var key = new StringBuilder(Normalize(message));
        if (history is { Count: > 0 })
        {
            foreach (var turn in history.Skip(Math.Max(0, history.Count - 3)))
            {
                key.Append('|').Append(Normalize(turn.Role)).Append(':').Append(Normalize(turn.Content));
            }
        }
        return key.ToString();
    }

    private sealed record Signal(IntentCategory Intent, double Confidence, string Reasoning = "", bool Failed = false);
}

/// <summary>Regex-based structured entity extraction.</summary>
public static partial class EntityExtractor
{
    public static IReadOnlyDictionary<string, IReadOnlyList<string>> Extract(string? message)
    {
        var text = message ?? "";
        return new Dictionary<string, IReadOnlyList<string>>
        {
            ["order_id"] = Unique(OrderIdRegex().Matches(text).Select(m => m.Groups[1].Value)),
            ["product"] = [],
            ["date"] = Unique(DateRegex().Matches(text).Select(m => m.Value)),
            ["error_code"] = Unique(HttpErrorRegex().Matches(text).Select(m => m.Value)
                .Concat(ErrorCodeRegex().Matches(text).Select(m => m.Value))),
            ["amount"] = Unique(AmountRegex().Matches(text).Select(m => m.Value)),
        };
    }

    private static List<string> Unique(IEnumerable<string> values) =>
        values.Where(v => !string.IsNullOrWhiteSpace(v)).Select(v => v.Trim()).Distinct().ToList();

    [GeneratedRegex(@"(?:订单号?|order(?:_id)?|#)\s*[:：#]?\s*([A-Za-z0-9_-]{4,32})", RegexOptions.IgnoreCase)]
    private static partial Regex OrderIdRegex();

    [GeneratedRegex(@"(今天|明天|昨天|本周|这周|下周|\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)", RegexOptions.IgnoreCase)]
    private static partial Regex DateRegex();

    // Explicit ASCII boundaries instead of \b: .NET treats Chinese characters as word characters,
    // so "\b500\b" would miss the code in "报500错误".
    [GeneratedRegex(@"(?<![A-Za-z0-9_])[45][0-9]{2}(?![A-Za-z0-9_])")]
    private static partial Regex HttpErrorRegex();

    [GeneratedRegex(@"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_-]{2,16}(?![A-Za-z0-9_])", RegexOptions.IgnoreCase)]
    private static partial Regex ErrorCodeRegex();

    [GeneratedRegex(@"((?:¥|￥)\s*\d+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?\s*(?:元|块|rmb|cny|usd|美元))", RegexOptions.IgnoreCase)]
    private static partial Regex AmountRegex();
}
