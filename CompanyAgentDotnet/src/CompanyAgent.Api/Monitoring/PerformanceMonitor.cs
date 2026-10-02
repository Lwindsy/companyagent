using System.Net.Http.Json;
using CompanyAgent.Api.Agents;
using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Tools;
using Microsoft.Extensions.Options;
using Prometheus;

namespace CompanyAgent.Api.Monitoring;

/// <summary>
/// Every 10 seconds: exports agent health to Prometheus, raises threshold alerts (optionally to a webhook)
/// and feeds a routing penalty back to the orchestrator so unhealthy agents are chosen less often.
/// </summary>
public sealed class PerformanceMonitor
{
    public const string WebhookHttpClient = "alert-webhook";

    private static readonly Gauge AgentSuccessRate = Prometheus.Metrics.CreateGauge(
        "companyagent_agent_success_rate", "Lowest success rate across agents.");
    private static readonly Counter Collections = Prometheus.Metrics.CreateCounter(
        "companyagent_monitor_collections_total", "Monitor collection runs.");
    private static readonly Histogram CollectionDuration = Prometheus.Metrics.CreateHistogram(
        "companyagent_monitor_collection_duration_seconds", "Monitor collection duration.");

    private readonly AgentOrchestrator _orchestrator;
    private readonly KnowledgeToolManager _tools;
    private readonly MonitorOptions _options;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILogger<PerformanceMonitor> _logger;
    private readonly object _lock = new();
    private readonly List<Dictionary<string, object>> _alerts = [];
    private readonly List<Dictionary<string, object>> _suggestions = [];
    private readonly HashSet<string> _alertKeys = [];
    private readonly HashSet<string> _suggestionKeys = [];

    public PerformanceMonitor(AgentOrchestrator orchestrator, KnowledgeToolManager tools, IOptions<CompanyAgentOptions> options,
        IHttpClientFactory httpClientFactory, ILogger<PerformanceMonitor> logger)
    {
        _orchestrator = orchestrator;
        _tools = tools;
        _options = options.Value.Monitor;
        _httpClientFactory = httpClientFactory;
        _logger = logger;
    }

    public async Task CollectAsync(CancellationToken cancellationToken)
    {
        using var timer = CollectionDuration.NewTimer();
        Collections.Inc();
        var penalties = new Dictionary<string, double>();
        var newAlerts = new List<Dictionary<string, object>>();
        var minSuccessRate = 1.0;

        foreach (var (key, stats) in _orchestrator.Stats())
        {
            minSuccessRate = Math.Min(minSuccessRate, stats.SuccessRate);
            if (stats.SuccessRate < _options.SuccessRateThreshold)
            {
                AddAlert($"agent_success_rate:{key}", stats.SuccessRate, _options.SuccessRateThreshold, newAlerts);
            }
            if (stats.AvgMs > _options.LatencyMsThreshold)
            {
                AddAlert($"agent_avg_ms:{key}", stats.AvgMs, _options.LatencyMsThreshold, newAlerts);
            }
            penalties[key] = RoutingPenalty(stats.SuccessRate, stats.AvgMs);
        }

        AgentSuccessRate.Set(minSuccessRate);
        _orchestrator.UpdateRoutingPenalties(penalties);
        if (penalties.Count > 0)
        {
            AddSuggestion("路由权重已根据在线表现调整", "检查 /monitor 中低成功率或高延迟 Agent，必要时优化 prompt 或增加实例。", 7);
        }
        foreach (var alert in newAlerts)
        {
            await SendWebhookAsync(alert, cancellationToken);
        }
    }

    public Dictionary<string, object> Summary()
    {
        lock (_lock)
        {
            return new Dictionary<string, object>
            {
                ["agent_stats"] = _orchestrator.Stats(),
                ["tool_stats"] = _tools.Stats(),
                ["active_alerts"] = _alerts.TakeLast(10).ToList(),
                ["suggestions"] = _suggestions.TakeLast(5).ToList(),
            };
        }
    }

    public List<Dictionary<string, object>> ActiveAlerts()
    {
        lock (_lock) return _alerts.TakeLast(10).ToList();
    }

    internal static double RoutingPenalty(double successRate, double avgMs)
    {
        var penalty = 0.0;
        if (successRate < 0.90) penalty += Math.Min(0.5, (0.90 - successRate) * 2);
        if (avgMs > 3000) penalty += Math.Min(0.4, (avgMs - 3000) / 10000);
        return Math.Min(0.9, penalty);
    }

    private void AddAlert(string metric, double value, double threshold, List<Dictionary<string, object>> newAlerts)
    {
        lock (_lock)
        {
            if (!_alertKeys.Add(metric)) return;
            var alert = new Dictionary<string, object>
            {
                ["metric"] = metric,
                ["value"] = value,
                ["threshold"] = threshold,
                ["resolved"] = false,
            };
            _alerts.Add(alert);
            newAlerts.Add(alert);
        }
    }

    private void AddSuggestion(string title, string action, int priority)
    {
        lock (_lock)
        {
            if (_suggestionKeys.Add(title))
            {
                _suggestions.Add(new Dictionary<string, object> { ["title"] = title, ["action"] = action, ["priority"] = priority });
            }
        }
    }

    private async Task SendWebhookAsync(Dictionary<string, object> alert, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_options.WebhookUrl)) return;
        try
        {
            var client = _httpClientFactory.CreateClient(WebhookHttpClient);
            using var response = await client.PostAsJsonAsync(_options.WebhookUrl, alert, cancellationToken);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning("Alert webhook failed: {Message}", ex.Message);
        }
    }
}

public sealed class MonitorWorker(PerformanceMonitor monitor, ILogger<MonitorWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(10));
        do
        {
            try
            {
                await monitor.CollectAsync(stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogWarning("Monitor collection failed: {Message}", ex.Message);
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }
}
