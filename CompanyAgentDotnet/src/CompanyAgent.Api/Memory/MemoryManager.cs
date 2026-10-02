using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Knowledge;
using CompanyAgent.Api.Llm;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Memory;

public enum MessageRole
{
    User,
    Assistant,
    System,
}

public sealed record ConversationMessage(MessageRole Role, string Content, DateTimeOffset Timestamp)
{
    [System.Text.Json.Serialization.JsonIgnore]
    public string RoleName => Role.ToString().ToLowerInvariant();
}

public sealed record MemoryContext(
    IReadOnlyList<ConversationMessage> RecentMessages,
    IReadOnlyList<string> RelevantHistory,
    JsonObject? UserProfile,
    string Summary)
{
    public string ToPromptText()
    {
        var prompt = new StringBuilder();
        if (!string.IsNullOrWhiteSpace(Summary))
        {
            prompt.Append("[会话摘要]\n").Append(Summary).Append("\n\n");
        }
        if (RelevantHistory.Count > 0)
        {
            prompt.Append("[相关历史]\n");
            foreach (var item in RelevantHistory.Take(3)) prompt.Append("- ").Append(item).Append('\n');
            prompt.Append('\n');
        }
        if (UserProfile is { Count: > 0 })
        {
            prompt.Append("[用户画像]\n").Append(UserProfile.ToJsonString(JsonText.Options)).Append("\n\n");
        }
        if (RecentMessages.Count > 0)
        {
            prompt.Append("[最近对话]\n");
            foreach (var m in RecentMessages) prompt.Append(m.RoleName).Append(": ").Append(m.Content).Append('\n');
        }
        return prompt.ToString().Trim();
    }
}

/// <summary>
/// Three memory layers: Redis working memory per conversation, episodic summaries produced when a
/// conversation grows past the compression threshold, and an LLM-extracted user profile.
/// Episodic memory and profiles are persisted to a JSON file.
/// </summary>
public sealed class MemoryManager
{
    private const int KeepAfterCompression = 5;

    private readonly IWorkingMemoryStore _store;
    private readonly ILlmGateway _llm;
    private readonly MemoryOptions _options;
    private readonly string _storePath;
    private readonly ILogger<MemoryManager> _logger;
    private readonly object _persistLock = new();
    private readonly List<EpisodicEntry> _episodic = [];
    private readonly Dictionary<string, JsonObject> _profiles = [];

    public MemoryManager(IWorkingMemoryStore store, ILlmGateway llm, IOptions<CompanyAgentOptions> options, ILogger<MemoryManager> logger)
    {
        _store = store;
        _llm = llm;
        _options = options.Value.Memory;
        _storePath = options.Value.Storage.MemoryPath;
        _logger = logger;
        LoadPersisted();
    }

    private TimeSpan Ttl => TimeSpan.FromSeconds(_options.TtlSeconds);

    public async Task<MemoryContext> GetContextAsync(string userId, string conversationId, string query)
    {
        var recent = await GetWorkingMemoryAsync(userId, conversationId);
        var summary = await SafeGetAsync(SummaryKey(userId, conversationId));
        JsonObject? profile;
        lock (_persistLock)
        {
            profile = _profiles.TryGetValue(userId, out var p) ? (JsonObject)p.DeepClone() : null;
        }
        return new MemoryContext(recent, SearchEpisodic(userId, query), profile, summary ?? "");
    }

    public async Task AddMessageAsync(string userId, string conversationId, MessageRole role, string content,
        CancellationToken cancellationToken = default)
    {
        var key = WorkingKey(userId, conversationId);
        try
        {
            var message = new ConversationMessage(role, content, DateTimeOffset.UtcNow);
            await _store.PushAsync(key, JsonSerializer.Serialize(message, JsonText.Options), Ttl);
            if (await _store.LengthAsync(key) >= _options.CompressAt)
            {
                await CompressAsync(userId, conversationId, cancellationToken);
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning("Failed to write working memory: {Message}", ex.Message);
        }
    }

    /// <summary>Oldest-first messages of the conversation, up to the working-memory limit.</summary>
    public async Task<List<ConversationMessage>> GetWorkingMemoryAsync(string userId, string conversationId)
    {
        try
        {
            var raw = await _store.RangeAsync(WorkingKey(userId, conversationId), _options.WorkingMax);
            return raw.Reverse()
                .Select(r => JsonSerializer.Deserialize<ConversationMessage>(r, JsonText.Options))
                .OfType<ConversationMessage>()
                .ToList();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Failed to read working memory: {Message}", ex.Message);
            return [];
        }
    }

    public async Task UpdateProfileAsync(string userId, string conversationId, CancellationToken cancellationToken)
    {
        var messages = await GetWorkingMemoryAsync(userId, conversationId);
        if (messages.Count == 0) return;
        var text = string.Join("\n", messages.TakeLast(10).Select(m => $"{m.RoleName}: {m.Content}"));
        var prompt = $$$"""
            从以下客服对话中提炼用户画像，返回 JSON：
            {{{text}}}
            格式: {"preferences":["..."],"entities":{"产品":[],"问题类型":[]}}
            """;
        try
        {
            var raw = await _llm.ChatAsync("", prompt, 0.0, 512, cancellationToken);
            var profile = JsonText.ExtractObject(raw) ?? throw new FormatException("No JSON object in profile reply");
            lock (_persistLock)
            {
                _profiles[userId] = profile;
                Persist();
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning("Profile update failed: {Message}", ex.Message);
        }
    }

    private async Task CompressAsync(string userId, string conversationId, CancellationToken cancellationToken)
    {
        var messages = await GetWorkingMemoryAsync(userId, conversationId);
        if (messages.Count < _options.CompressAt) return;
        var keep = Math.Min(KeepAfterCompression, messages.Count);
        var old = messages.Take(messages.Count - keep).ToList();
        var kept = messages.Skip(messages.Count - keep).ToList();
        var text = string.Join("\n", old.Select(m => $"{m.RoleName}: {m.Content}"));

        string summary;
        try
        {
            summary = await _llm.ChatAsync("", "用 2-3 句话总结以下对话关键信息：\n" + text, 0.0, 256, cancellationToken);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            summary = $"对话包含 {old.Count} 条历史消息。";
        }

        lock (_persistLock)
        {
            _episodic.Add(new EpisodicEntry(userId, conversationId, summary, text, DateTimeOffset.UtcNow));
            Persist();
        }

        var summaryKey = SummaryKey(userId, conversationId);
        var oldSummary = await SafeGetAsync(summaryKey);
        await _store.SetAsync(summaryKey, (string.IsNullOrEmpty(oldSummary) ? summary : oldSummary + "\n" + summary).Trim(), Ttl);
        var newestFirst = kept.AsEnumerable().Reverse().Select(m => JsonSerializer.Serialize(m, JsonText.Options)).ToList();
        await _store.ReplaceAsync(WorkingKey(userId, conversationId), newestFirst, Ttl);
    }

    private List<string> SearchEpisodic(string userId, string? query)
    {
        if (string.IsNullOrWhiteSpace(query)) return [];
        var queryVector = Embed(query);
        lock (_persistLock)
        {
            return _episodic
                .Where(e => e.UserId == userId)
                .OrderByDescending(e => TextVectors.Cosine(queryVector, e.Vector))
                .Take(5)
                .Select(e => e.Summary)
                .ToList();
        }
    }

    private async Task<string?> SafeGetAsync(string key)
    {
        try
        {
            return await _store.GetAsync(key);
        }
        catch (Exception)
        {
            return null;
        }
    }

    // Char 1-3 grams, as in the Java backend's episodic memory.
    private static double[] Embed(string text)
    {
        var normalized = text.ToLowerInvariant();
        var grams = new List<string>();
        for (var n = 1; n <= 3; n++)
        {
            for (var i = 0; i + n <= normalized.Length; i++) grams.Add(normalized.Substring(i, n));
        }
        return TextVectors.Embed(grams);
    }

    // Prefixed with "dotnet:" so the three backends can share one Redis without clobbering each other.
    private static string WorkingKey(string userId, string conversationId) => $"dotnet:wm:{userId}:{conversationId}";

    private static string SummaryKey(string userId, string conversationId) => $"dotnet:summary:{userId}:{conversationId}";

    private void LoadPersisted()
    {
        if (!File.Exists(_storePath)) return;
        try
        {
            var stored = JsonSerializer.Deserialize<StoredMemory>(File.ReadAllText(_storePath), JsonText.Options);
            foreach (var e in stored?.Episodic ?? [])
            {
                if (string.IsNullOrWhiteSpace(e.Summary)) continue;
                _episodic.Add(new EpisodicEntry(e.UserId ?? "", e.ConversationId ?? "", e.Summary, e.FullText ?? "",
                    e.Timestamp ?? DateTimeOffset.UtcNow));
            }
            foreach (var (user, profile) in stored?.Profiles ?? []) _profiles[user] = profile;
            _logger.LogInformation("Loaded persisted memory: episodic={Episodic}, profiles={Profiles}", _episodic.Count, _profiles.Count);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Failed to load memory store {Path}: {Message}", _storePath, ex.Message);
        }
    }

    // Caller holds _persistLock.
    private void Persist()
    {
        try
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(_storePath));
            if (dir is not null) Directory.CreateDirectory(dir);
            var stored = new StoredMemory(
                _episodic.Select(e => new StoredEpisodic(e.UserId, e.ConversationId, e.Summary, e.FullText, e.Timestamp)).ToList(),
                _profiles.ToDictionary(kv => kv.Key, kv => kv.Value));
            var tmp = _storePath + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(stored, JsonText.Indented));
            File.Move(tmp, _storePath, overwrite: true);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Failed to persist memory store {Path}: {Message}", _storePath, ex.Message);
        }
    }

    private sealed record EpisodicEntry(string UserId, string ConversationId, string Summary, string FullText, DateTimeOffset Timestamp)
    {
        public double[] Vector { get; } = Embed(Summary);
    }

    private sealed record StoredMemory(List<StoredEpisodic>? Episodic, Dictionary<string, JsonObject>? Profiles);

    private sealed record StoredEpisodic(string? UserId, string? ConversationId, string? Summary, string? FullText, DateTimeOffset? Timestamp);
}
