namespace CompanyAgent.Api.Configuration;

/// <summary>
/// Runtime settings. Values are read from the same environment variable names the
/// Java and Python backends use, so all three can share one .env file in Docker.
/// </summary>
public sealed class CompanyAgentOptions
{
    public LlmOptions Llm { get; set; } = new();
    public RedisOptions Redis { get; set; } = new();
    public MemoryOptions Memory { get; set; } = new();
    public RagOptions Rag { get; set; } = new();
    public StorageOptions Storage { get; set; } = new();
    public MonitorOptions Monitor { get; set; } = new();
    public EvalOptions Eval { get; set; } = new();
    public SkillsOptions Skills { get; set; } = new();
    public AdminOptions Admin { get; set; } = new();

    public static CompanyAgentOptions FromConfiguration(IConfiguration config)
    {
        string Get(string key, string fallback)
        {
            // An empty value (e.g. "ANTHROPIC_BASE_URL=") means "not set", not "use an empty string".
            var value = config[key];
            return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
        }

        int GetInt(string key, int fallback) => int.TryParse(config[key], out var v) ? v : fallback;
        double GetDouble(string key, double fallback) =>
            double.TryParse(config[key], System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : fallback;
        bool GetBool(string key, bool fallback) => bool.TryParse(config[key], out var v) ? v : fallback;

        var dataDir = Get("COMPANYAGENT_DATA_DIR", "data/dotnet");
        return new CompanyAgentOptions
        {
            Llm = new LlmOptions
            {
                // LLM_PROVIDER is the .NET name; SPRING_PROFILES_ACTIVE is accepted so the shared .env keeps working.
                Provider = Get("LLM_PROVIDER", Get("SPRING_PROFILES_ACTIVE", "anthropic")).ToLowerInvariant(),
                FallbackEnabled = GetBool("LLM_FALLBACK_ENABLED", true),
                AnthropicApiKey = Get("ANTHROPIC_API_KEY", ""),
                AnthropicBaseUrl = Get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                AnthropicModel = Get("ANTHROPIC_MODEL", "claude-opus-5-5"),
                AnthropicEffort = Get("ANTHROPIC_EFFORT", "low").ToLowerInvariant(),
                DeepSeekApiKey = Get("DEEPSEEK_API_KEY", ""),
                DeepSeekBaseUrl = Get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                DeepSeekModel = Get("DEEPSEEK_MODEL", "deepseek-chat"),
            },
            Redis = new RedisOptions
            {
                Host = Get("REDIS_HOST", "localhost"),
                Port = GetInt("REDIS_PORT", 6379),
                Password = Get("REDIS_PASSWORD", ""),
            },
            Memory = new MemoryOptions
            {
                TtlSeconds = GetInt("MEMORY_TTL_SECONDS", 86400),
                WorkingMax = GetInt("MEMORY_WORKING_MAX", 20),
                CompressAt = GetInt("MEMORY_COMPRESS_AT", 15),
            },
            Rag = new RagOptions
            {
                TopK = GetInt("RAG_TOP_K", 4),
                Bm25Weight = GetDouble("RAG_BM25_WEIGHT", 0.45),
                VectorWeight = GetDouble("RAG_VECTOR_WEIGHT", 0.55),
            },
            Storage = new StorageOptions
            {
                DataDir = dataDir,
                KnowledgePath = Get("KNOWLEDGE_STORE_PATH", Path.Combine(dataDir, "knowledge-store.json")),
                MemoryPath = Get("MEMORY_STORE_PATH", Path.Combine(dataDir, "memory-store.json")),
            },
            Monitor = new MonitorOptions
            {
                SuccessRateThreshold = GetDouble("MONITOR_SUCCESS_RATE_THRESHOLD", 0.90),
                LatencyMsThreshold = GetDouble("MONITOR_LATENCY_MS_THRESHOLD", 3000),
                WebhookUrl = Get("ALERT_WEBHOOK_URL", ""),
            },
            Eval = new EvalOptions { BaselinePath = Get("EVAL_BASELINE_PATH", "data/eval/baseline.json") },
            Skills = new SkillsOptions
            {
                RootDir = Get("COMPANYAGENT_SKILLS_DIR", "skills"),
                MaxPromptChars = GetInt("COMPANYAGENT_SKILLS_MAX_PROMPT_CHARS", 5000),
            },
            Admin = new AdminOptions
            {
                Username = Get("ADMIN_USERNAME", "admin"),
                // No built-in default: administrator sign-in stays disabled until ADMIN_PASSWORD is set.
                Password = Get("ADMIN_PASSWORD", ""),
            },
        };
    }
}

public sealed class LlmOptions
{
    public string Provider { get; set; } = "anthropic";
    public bool FallbackEnabled { get; set; } = true;
    public string AnthropicApiKey { get; set; } = "";
    public string AnthropicBaseUrl { get; set; } = "https://api.anthropic.com";
    public string AnthropicModel { get; set; } = "claude-opus-5-5";
    public string AnthropicEffort { get; set; } = "low";
    public string DeepSeekApiKey { get; set; } = "";
    public string DeepSeekBaseUrl { get; set; } = "https://api.deepseek.com";
    public string DeepSeekModel { get; set; } = "deepseek-chat";

    public bool UsesDeepSeek => Provider.Contains("deepseek", StringComparison.Ordinal);
}

public sealed class RedisOptions
{
    public string Host { get; set; } = "localhost";
    public int Port { get; set; } = 6379;
    public string Password { get; set; } = "";
}

public sealed class MemoryOptions
{
    public int TtlSeconds { get; set; } = 86400;
    public int WorkingMax { get; set; } = 20;
    public int CompressAt { get; set; } = 15;
}

public sealed class RagOptions
{
    public int TopK { get; set; } = 4;
    public double Bm25Weight { get; set; } = 0.45;
    public double VectorWeight { get; set; } = 0.55;
}

public sealed class StorageOptions
{
    public string DataDir { get; set; } = "data/dotnet";
    public string KnowledgePath { get; set; } = "data/dotnet/knowledge-store.json";
    public string MemoryPath { get; set; } = "data/dotnet/memory-store.json";
}

public sealed class MonitorOptions
{
    public double SuccessRateThreshold { get; set; } = 0.90;
    public double LatencyMsThreshold { get; set; } = 3000;
    public string WebhookUrl { get; set; } = "";
}

public sealed class EvalOptions
{
    public string BaselinePath { get; set; } = "data/eval/baseline.json";
}

public sealed class SkillsOptions
{
    public string RootDir { get; set; } = "skills";
    public int MaxPromptChars { get; set; } = 5000;
}

public sealed class AdminOptions
{
    public string Username { get; set; } = "admin";
    public string Password { get; set; } = "";

    public bool Configured => !string.IsNullOrEmpty(Password);
}
