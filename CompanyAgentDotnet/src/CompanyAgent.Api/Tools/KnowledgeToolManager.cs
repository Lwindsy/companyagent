using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Knowledge;
using CompanyAgent.Api.Llm;
using Polly;
using Polly.CircuitBreaker;
using Polly.Timeout;

namespace CompanyAgent.Api.Tools;

public sealed record ToolResult<T>(bool Success, T Data, string ToolName, string? Error, bool Cached, long LatencyMs, bool Reranked);

/// <summary>Thread-safe call statistics for one tool.</summary>
public sealed class ToolStats
{
    private readonly object _lock = new();
    private long _total, _success, _totalLatencyMs;
    private int _consecutiveFails;

    public void Record(bool ok, long latencyMs)
    {
        lock (_lock)
        {
            _total++;
            _totalLatencyMs += Math.Max(0, latencyMs);
            if (ok) { _success++; _consecutiveFails = 0; }
            else { _consecutiveFails++; }
        }
    }

    public (long Total, double SuccessRate, double AvgLatencyMs, int ConsecutiveFails) Snapshot()
    {
        lock (_lock)
        {
            return (_total,
                _total == 0 ? 1.0 : (double)_success / _total,
                _total == 0 ? 0.0 : (double)_totalLatencyMs / _total,
                _consecutiveFails);
        }
    }
}

/// <summary>
/// The knowledge_search tool: LLM query rewriting, parallel recall over the sub-queries, LLM rerank,
/// a 5-minute result cache, and a Polly pipeline (30 s timeout + circuit breaker) with a degraded result.
/// </summary>
public sealed class KnowledgeToolManager
{
    public const string ToolName = "knowledge_search";
    private static readonly TimeSpan CacheTtl = TimeSpan.FromMinutes(5);

    private readonly KnowledgeBaseService _knowledge;
    private readonly ILlmGateway _llm;
    private readonly ToolStats _stats = new();
    private readonly ConcurrentDictionary<string, (List<SearchResult> Results, DateTimeOffset ExpiresAt)> _cache = new();
    private readonly CircuitBreakerStateProvider _circuitState = new();
    private readonly ResiliencePipeline _pipeline;

    public KnowledgeToolManager(KnowledgeBaseService knowledge, ILlmGateway llm)
    {
        _knowledge = knowledge;
        _llm = llm;
        _pipeline = new ResiliencePipelineBuilder()
            .AddCircuitBreaker(new CircuitBreakerStrategyOptions
            {
                // Open after 5 calls in a row fail within a minute; probe again after 60 s.
                FailureRatio = 1.0,
                MinimumThroughput = 5,
                SamplingDuration = TimeSpan.FromSeconds(60),
                BreakDuration = TimeSpan.FromSeconds(60),
                StateProvider = _circuitState,
                ShouldHandle = new PredicateBuilder().Handle<Exception>(ex => ex is not ArgumentException),
            })
            .AddTimeout(TimeSpan.FromSeconds(30))
            .Build();
    }

    public async Task<ToolResult<List<SearchResult>>> SearchWithRewriteAsync(string query, int topK,
        CancellationToken cancellationToken = default)
    {
        Validate(query, topK);
        var stopwatch = Stopwatch.StartNew();
        try
        {
            var results = await _pipeline.ExecuteAsync(async ct =>
            {
                var queries = await RewriteQueryAsync(query, ct);
                var recallK = Math.Max(topK, 5);
                var recalls = await Task.WhenAll(queries.Select(q => Task.Run(() => Search(q, recallK).Data, ct)));
                var merged = recalls.SelectMany(r => r).DistinctBy(r => r.Id).ToList();
                return await RerankAsync(query, merged, topK, ct);
            }, cancellationToken);

            _stats.Record(true, stopwatch.ElapsedMilliseconds);
            return new ToolResult<List<SearchResult>>(true, results, ToolName, null, false, stopwatch.ElapsedMilliseconds, true);
        }
        catch (BrokenCircuitException)
        {
            return new ToolResult<List<SearchResult>>(true, Fallback(query, "工具熔断中，请稍后重试"), ToolName, "circuit open", false, 0, false);
        }
        catch (TimeoutRejectedException)
        {
            _stats.Record(false, stopwatch.ElapsedMilliseconds);
            return new ToolResult<List<SearchResult>>(true, Fallback(query, "执行超时"), ToolName, "timeout", false, stopwatch.ElapsedMilliseconds, false);
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
        {
            _stats.Record(false, stopwatch.ElapsedMilliseconds);
            return new ToolResult<List<SearchResult>>(true, Fallback(query, ex.Message), ToolName, ex.Message, false, stopwatch.ElapsedMilliseconds, false);
        }
    }

    public ToolResult<List<SearchResult>> Search(string query, int topK)
    {
        Validate(query, topK);
        // The knowledge version is part of the key, so adding documents invalidates older cached results.
        var key = $"{_knowledge.Version}:{topK}:{query}";
        if (_cache.TryGetValue(key, out var cached) && cached.ExpiresAt > DateTimeOffset.UtcNow)
        {
            return new ToolResult<List<SearchResult>>(true, cached.Results, ToolName, null, true, 0, false);
        }
        var stopwatch = Stopwatch.StartNew();
        var results = _knowledge.Search(query, topK);
        if (_cache.Count > 1000)
        {
            _cache.Clear(); // bounded: entries from older knowledge versions are never read again
        }
        _cache[key] =(results, DateTimeOffset.UtcNow + CacheTtl);
        return new ToolResult<List<SearchResult>>(true, results, ToolName, null, false, stopwatch.ElapsedMilliseconds, false);
    }

    public Dictionary<string, object> Stats()
    {
        var s = _stats.Snapshot();
        return new Dictionary<string, object>
        {
            [ToolName] = new Dictionary<string, object>
            {
                ["total"] = s.Total,
                ["success_rate"] = JsonText.Round(s.SuccessRate, 3),
                ["avg_latency_ms"] = JsonText.Round(s.AvgLatencyMs, 3),
                ["consecutive_fails"] = s.ConsecutiveFails,
                ["circuit_state"] = CircuitStateName(_circuitState.CircuitState),
            },
        };
    }

    private async Task<List<string>> RewriteQueryAsync(string query, CancellationToken cancellationToken)
    {
        var prompt = $"""
            将以下用户查询改写为 3 个不同角度的知识库搜索子查询，返回 JSON 数组。
            原始查询: "{query}"
            """;
        try
        {
            var raw = await _llm.ChatAsync("", prompt, 0.3, 256, cancellationToken);
            var rewritten = JsonText.ExtractArray(raw)?.Select(n => n?.ToString()).OfType<string>() ?? [];
            return new[] { query }.Concat(rewritten.Where(q => !string.IsNullOrWhiteSpace(q))).Distinct().ToList();
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return [query];
        }
    }

    private async Task<List<SearchResult>> RerankAsync(string query, List<SearchResult> results, int topK,
        CancellationToken cancellationToken)
    {
        if (results.Count <= topK) return results;
        var items = new StringBuilder();
        for (var i = 0; i < results.Count; i++)
        {
            var content = results[i].Content;
            items.Append(i).Append(". ").Append(results[i].Title).Append(" - ")
                .Append(content.Length > 180 ? content[..180] : content).Append('\n');
        }
        var prompt = $"""
            根据用户查询，对以下检索结果按相关性排序，只返回 JSON 索引数组。
            用户查询: "{query}"
            检索结果:
            {items}
            示例返回: [2,0,1]
            """;
        try
        {
            var raw = await _llm.ChatAsync("", prompt, 0.0, 256, cancellationToken);
            var order = JsonText.ExtractArray(raw)?
                .Select(n => JsonText.AsDouble(n))
                .Where(d => d is >= 0 && d < results.Count)
                .Select(d => results[(int)d!.Value])
                .DistinctBy(r => r.Id)
                .Take(topK)
                .ToList();
            if (order is { Count: > 0 }) return order;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // fall through to score order
        }
        return results.OrderByDescending(r => r.Score).Take(topK).ToList();
    }

    private static void Validate(string query, int topK)
    {
        if (string.IsNullOrWhiteSpace(query)) throw new ArgumentException("query 不能为空");
        if (topK is < 1 or > 50) throw new ArgumentException("topK 必须在 1 到 50 之间");
    }

    private static List<SearchResult> Fallback(string query, string? error) =>
    [
        new SearchResult("fallback", "知识库降级结果", $"知识库暂时不可用，未能完成对“{query}”的检索。请稍后重试，或转人工客服确认。",
            0.0, 0, new Dictionary<string, object> { ["fallback"] = true, ["error"] = error ?? "" }),
    ];

    private static string CircuitStateName(CircuitState state) => state switch
    {
        CircuitState.Closed => "closed",
        CircuitState.Open or CircuitState.Isolated => "open",
        CircuitState.HalfOpen => "half_open",
        _ => state.ToString().ToLowerInvariant(),
    };
}
