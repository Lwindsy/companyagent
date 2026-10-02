using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Anthropic;
using Anthropic.Exceptions;
using Anthropic.Models.Beta.Messages;
using CompanyAgent.Api.Configuration;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Llm;

/// <summary>
/// Routes calls to Claude (official Anthropic SDK) or DeepSeek (OpenAI-compatible REST) and,
/// when enabled, replaces any failure with a deterministic local reply so the chat flow keeps working.
/// </summary>
public sealed class LlmGateway : ILlmGateway
{
    public const string DeepSeekHttpClient = "deepseek";

    private readonly LlmOptions _options;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILogger<LlmGateway> _logger;
    private readonly Lazy<AnthropicClient> _anthropic;

    public LlmGateway(IOptions<CompanyAgentOptions> options, IHttpClientFactory httpClientFactory, ILogger<LlmGateway> logger)
    {
        _options = options.Value.Llm;
        _httpClientFactory = httpClientFactory;
        _logger = logger;
        _anthropic = new Lazy<AnthropicClient>(() => new AnthropicClient
        {
            ApiKey = _options.AnthropicApiKey,
            BaseUrl = _options.AnthropicBaseUrl,
            Timeout = TimeSpan.FromSeconds(60),
            MaxRetries = 2,
        });
    }

    public bool CredentialsConfigured => _options.UsesDeepSeek
        ? !string.IsNullOrWhiteSpace(_options.DeepSeekApiKey)
        : !string.IsNullOrWhiteSpace(_options.AnthropicApiKey);

    public async Task<string> ChatAsync(string systemPrompt, string userPrompt, double temperature, int maxTokens,
        CancellationToken cancellationToken = default)
    {
        try
        {
            if (!CredentialsConfigured)
            {
                throw new InvalidOperationException($"No API key configured for LLM provider '{_options.Provider}'");
            }
            return _options.UsesDeepSeek
                ? await CallDeepSeekAsync(systemPrompt, userPrompt, temperature, maxTokens, cancellationToken)
                : await CallAnthropicAsync(systemPrompt, userPrompt, temperature, maxTokens, cancellationToken);
        }
        catch (Exception ex) when (_options.FallbackEnabled && ex is not OperationCanceledException)
        {
            _logger.LogWarning("LLM call failed, using deterministic fallback: {Message}", ex.Message);
            return LlmFallback.Reply(userPrompt);
        }
    }

    private async Task<string> CallAnthropicAsync(string systemPrompt, string userPrompt, double temperature,
        int maxTokens, CancellationToken cancellationToken)
    {
        var request = BuildAnthropicRequest(_options, systemPrompt, userPrompt, temperature, maxTokens);
        var response = await _anthropic.Value.Beta.Messages.Create(request, cancellationToken);
        if (response.StopReason == "refusal")
        {
            throw new InvalidOperationException("Claude declined the request (stop_reason=refusal)");
        }

        return string.Concat(response.Content
            .Select(block => block.Value)
            .OfType<BetaTextBlock>()
            .Select(block => block.Text));
    }

    internal static MessageCreateParams BuildAnthropicRequest(LlmOptions options, string systemPrompt, string userPrompt,
        double temperature, int maxTokens)
    {
        var model = options.AnthropicModel;
        var capabilities = ClaudeModelCapabilities.For(model);
        var request = new MessageCreateParams
        {
            Model = model,
            // Models that always think spend part of max_tokens on thinking, so small classification
            // budgets (256) would truncate the JSON answer. Give them a floor.
            MaxTokens = capabilities.AlwaysThinks ? Math.Max(maxTokens, 4096) : maxTokens,
            Messages = [new BetaMessageParam { Role = Role.User, Content = userPrompt }],
        };
        if (!string.IsNullOrWhiteSpace(systemPrompt))
        {
            request = request with { System = systemPrompt };
        }
        if (capabilities.AcceptsTemperature)
        {
            // Only older models get here; the SDK marks the property obsolete because newer models reject it.
#pragma warning disable CS0618
            request = request with { Temperature = temperature };
#pragma warning restore CS0618
        }
        if (capabilities.SupportsEffort)
        {
            request = request with { OutputConfig = new BetaOutputConfig { Effort = ParseEffort(options.AnthropicEffort) } };
        }
        if (capabilities.SupportsServerFallback && IsFirstPartyApi(options.AnthropicBaseUrl))
        {
            // A safety-classifier refusal is re-served by a fallback model inside the same call.
            request = request with { Betas = ["server-side-fallback-2026-07-01"], Fallbacks = new Default() };
        }
        return request;
    }

    private async Task<string> CallDeepSeekAsync(string systemPrompt, string userPrompt, double temperature,
        int maxTokens, CancellationToken cancellationToken)
    {
        var messages = new JsonArray();
        if (!string.IsNullOrWhiteSpace(systemPrompt))
        {
            messages.Add(new JsonObject { ["role"] = "system", ["content"] = systemPrompt });
        }
        messages.Add(new JsonObject { ["role"] = "user", ["content"] = userPrompt });
        var body = new JsonObject
        {
            ["model"] = _options.DeepSeekModel,
            ["messages"] = messages,
            ["temperature"] = temperature,
            ["max_tokens"] = maxTokens,
        };

        var client = _httpClientFactory.CreateClient(DeepSeekHttpClient);
        using var httpRequest = new HttpRequestMessage(HttpMethod.Post, $"{_options.DeepSeekBaseUrl.TrimEnd('/')}/chat/completions")
        {
            Content = JsonContent.Create(body),
        };
        httpRequest.Headers.Authorization = new("Bearer", _options.DeepSeekApiKey);
        using var httpResponse = await client.SendAsync(httpRequest, cancellationToken);
        httpResponse.EnsureSuccessStatusCode();
        var json = await httpResponse.Content.ReadFromJsonAsync<JsonObject>(cancellationToken);
        return json?["choices"]?[0]?["message"]?["content"]?.GetValue<string>() ?? "";
    }

    // Proxies and other gateways may not accept the fallback beta, so it is only sent to the Claude API itself.
    private static bool IsFirstPartyApi(string baseUrl) =>
        Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) && uri.Host == "api.anthropic.com";

    private static Effort ParseEffort(string value) => value switch
    {
        "medium" => Effort.Medium,
        "high" => Effort.High,
        "max" => Effort.Max,
        _ => Effort.Low,
    };
}

/// <summary>Request-shape differences between Claude model generations.</summary>
public sealed record ClaudeModelCapabilities(bool AlwaysThinks, bool AcceptsTemperature, bool SupportsEffort, bool SupportsServerFallback)
{
    private static readonly string[] CurrentGeneration =
        ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7"];

    private static readonly string[] ServerFallbackModels =
        ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"];

    public static ClaudeModelCapabilities For(string model)
    {
        var current = CurrentGeneration.Any(prefix => model.StartsWith(prefix, StringComparison.Ordinal));
        var effort = current || model.StartsWith("claude-opus-4-6", StringComparison.Ordinal)
                             || model.StartsWith("claude-sonnet-4-6", StringComparison.Ordinal);
        return new ClaudeModelCapabilities(
            AlwaysThinks: current,
            // Current-generation models reject sampling parameters with a 400.
            AcceptsTemperature: !current,
            SupportsEffort: effort,
            SupportsServerFallback: ServerFallbackModels.Contains(model));
    }
}
