using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Llm;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Tests;

/// <summary>LLM stub: returns the first reply whose trigger appears in the prompt, else a default.</summary>
internal sealed class ScriptedLlm : ILlmGateway
{
    private readonly List<(string Trigger, Func<string> Reply)> _rules = [];
    private readonly Func<string> _default;

    public ScriptedLlm(string defaultReply = "") => _default = () => defaultReply;

    public ScriptedLlm On(string trigger, string reply)
    {
        _rules.Add((trigger, () => reply));
        return this;
    }

    public ScriptedLlm Throw(string trigger)
    {
        _rules.Add((trigger, () => throw new HttpRequestException("model down")));
        return this;
    }

    public List<string> Prompts { get; } = [];

    public Task<string> ChatAsync(string systemPrompt, string userPrompt, double temperature, int maxTokens,
        CancellationToken cancellationToken = default)
    {
        lock (Prompts) Prompts.Add(userPrompt);
        foreach (var (trigger, reply) in _rules)
        {
            if (userPrompt.Contains(trigger)) return Task.FromResult(reply());
        }
        return Task.FromResult(_default());
    }
}

internal sealed class ManualTimeProvider(DateTimeOffset start) : TimeProvider
{
    public DateTimeOffset Now { get; set; } = start;

    public override DateTimeOffset GetUtcNow() => Now;
}

internal static class TestOptions
{
    public static IOptions<CompanyAgentOptions> Create(Action<CompanyAgentOptions>? configure = null)
    {
        var dir = Path.Combine(Path.GetTempPath(), "companyagent-tests", Guid.NewGuid().ToString("N"));
        var options = new CompanyAgentOptions
        {
            Storage = new StorageOptions
            {
                DataDir = dir,
                KnowledgePath = Path.Combine(dir, "knowledge-store.json"),
                MemoryPath = Path.Combine(dir, "memory-store.json"),
            },
            Eval = new EvalOptions { BaselinePath = Path.Combine(dir, "baseline.json") },
            Skills = new SkillsOptions { RootDir = Path.Combine(dir, "skills") },
        };
        configure?.Invoke(options);
        return Options.Create(options);
    }
}
