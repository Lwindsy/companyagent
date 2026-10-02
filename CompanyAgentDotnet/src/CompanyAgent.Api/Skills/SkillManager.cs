using System.Text.Json.Nodes;
using CompanyAgent.Api.Configuration;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Skills;

public sealed record Skill(
    string Name,
    string Description,
    string Content,
    string Path,
    IReadOnlyList<string> Keywords,
    IReadOnlyList<string> Agents,
    bool Enabled)
{
    public bool Matches(string? message, string agentType)
    {
        if (!Enabled) return false;
        if (Agents.Count > 0 && !Agents.Contains(agentType.ToLowerInvariant())) return false;
        if (Keywords.Count == 0) return true;
        var lowered = (message ?? "").ToLowerInvariant();
        return Keywords.Any(keyword => lowered.Contains(keyword.ToLowerInvariant()));
    }

    public string ToPromptBlock(int maxChars)
    {
        var body = Content.Trim();
        if (body.Length > maxChars)
        {
            body = body[..maxChars].TrimEnd() + "\n...";
        }
        var desc = string.IsNullOrWhiteSpace(Description) ? "" : "\n说明: " + Description;
        return $"### {Name}{desc}\n{body}";
    }

    public object Summary() => new Dictionary<string, object>
    {
        ["name"] = Name,
        ["description"] = Description,
        ["path"] = Path,
        ["keywords"] = Keywords,
        ["agents"] = Agents,
        ["enabled"] = Enabled,
        ["content_chars"] = Content.Length,
    };
}

/// <summary>
/// Loads hot-reloadable business rules from SKILL.md (front matter + Markdown), other .md/.txt files
/// and .json files, and injects the ones matching the message and agent into the system prompt.
/// </summary>
public sealed class SkillManager
{
    private static readonly HashSet<string> SupportedSuffixes = [".md", ".txt", ".json"];
    private static readonly HashSet<string> FalseValues = ["0", "false", "no", "off", "disabled"];

    private readonly SkillsOptions _options;
    private readonly object _loadLock = new();
    private volatile IReadOnlyList<Skill> _skills = [];
    private volatile IReadOnlyList<string> _errors = [];

    public SkillManager(IOptions<CompanyAgentOptions> options)
    {
        _options = options.Value.Skills;
        Load();
    }

    private string RootDir => System.IO.Path.GetFullPath(_options.RootDir);

    public IReadOnlyList<Skill> Load()
    {
        lock (_loadLock)
        {
            var loaded = new List<Skill>();
            var errors = new List<string>();
            if (Directory.Exists(RootDir))
            {
                foreach (var path in DiscoverFiles(RootDir))
                {
                    try
                    {
                        if (LoadFile(path) is { } skill) loaded.Add(skill);
                    }
                    catch (Exception ex)
                    {
                        errors.Add($"{path}: {ex.Message}");
                    }
                }
            }
            // Swap both lists at once so concurrent readers never see a half-built set.
            _skills = loaded;
            _errors = errors;
            return loaded;
        }
    }

    public string PromptFor(string? message, string agentType)
    {
        var blocks = new List<string>();
        var remaining = _options.MaxPromptChars;
        foreach (var skill in _skills)
        {
            if (!skill.Matches(message, agentType)) continue;
            var block = skill.ToPromptBlock(Math.Min(3200, remaining));
            if (block.Length > remaining)
            {
                block = block[..remaining].TrimEnd() + "\n...";
            }
            blocks.Add(block);
            remaining -= block.Length;
            if (remaining <= 0) break;
        }
        if (blocks.Count == 0) return "";
        return "以下是当前请求可用的 CompanyAgent Skills。请优先遵循这些业务规则；如果与系统角色冲突，以系统角色和安全边界为准。\n\n"
               + string.Join("\n\n", blocks);
    }

    public object Summary() => new Dictionary<string, object>
    {
        ["root_dir"] = RootDir,
        ["count"] = _skills.Count,
        ["skills"] = _skills.Select(s => s.Summary()).ToList(),
        ["errors"] = _errors,
    };

    private static List<string> DiscoverFiles(string root)
    {
        var all = Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
            .OrderBy(p => p, StringComparer.Ordinal)
            .ToList();
        var skillFiles = all.Where(p => System.IO.Path.GetFileName(p) == "SKILL.md").ToList();
        var others = all
            .Where(p => !skillFiles.Contains(p))
            .Where(p => !System.IO.Path.GetFileName(p).StartsWith('.'))
            .Where(p => !System.IO.Path.GetFileName(p).Equals("README.md", StringComparison.OrdinalIgnoreCase))
            .Where(p => SupportedSuffixes.Contains(System.IO.Path.GetExtension(p).ToLowerInvariant()));
        return skillFiles.Concat(others).ToList();
    }

    private static Skill? LoadFile(string path) =>
        System.IO.Path.GetExtension(path).Equals(".json", StringComparison.OrdinalIgnoreCase) ? LoadJson(path) : LoadText(path);

    private static Skill LoadJson(string path)
    {
        var raw = JsonNode.Parse(File.ReadAllText(path)) as JsonObject
                  ?? throw new FormatException("JSON skill must be an object");
        var content = (raw["content"] ?? raw["instructions"])?.ToString().Trim() ?? "";
        if (content.Length == 0) throw new FormatException("缺少 content 或 instructions");
        return new Skill(
            raw["name"]?.ToString() ?? System.IO.Path.GetFileNameWithoutExtension(path),
            raw["description"]?.ToString() ?? "",
            content,
            path,
            AsList(raw["keywords"]),
            AsList(raw["agents"]).Select(a => a.ToLowerInvariant()).ToList(),
            AsBool(raw["enabled"]?.ToString(), true));
    }

    private static Skill? LoadText(string path)
    {
        var (meta, rawBody) = SplitFrontMatter(File.ReadAllText(path));
        var body = rawBody.Trim();
        if (body.Length == 0) return null;
        var defaultName = System.IO.Path.GetFileName(path) == "SKILL.md"
            ? new DirectoryInfo(System.IO.Path.GetDirectoryName(path)!).Name
            : System.IO.Path.GetFileNameWithoutExtension(path);
        var name = meta.GetValueOrDefault("name") ?? FirstHeading(body);
        if (string.IsNullOrWhiteSpace(name)) name = defaultName;
        return new Skill(
            name,
            meta.GetValueOrDefault("description") ?? "",
            StripFirstHeading(body, name),
            path,
            SplitCsv(meta.GetValueOrDefault("keywords")),
            SplitCsv(meta.GetValueOrDefault("agents")).Select(a => a.ToLowerInvariant()).ToList(),
            AsBool(meta.GetValueOrDefault("enabled"), true));
    }

    internal static (Dictionary<string, string> Meta, string Body) SplitFrontMatter(string raw)
    {
        var lines = raw.TrimStart().Split(["\r\n", "\n"], StringSplitOptions.None);
        if (lines.Length == 0 || lines[0].Trim() != "---") return ([], raw);
        var meta = new Dictionary<string, string>();
        for (var i = 1; i < lines.Length; i++)
        {
            var line = lines[i].Trim();
            if (line == "---")
            {
                return (meta, string.Join("\n", lines.Skip(i + 1)));
            }
            var sep = line.IndexOf(':');
            if (sep > 0)
            {
                meta[line[..sep].Trim()] = line[(sep + 1)..].Trim().Trim('\'', '"');
            }
        }
        return ([], raw); // unterminated front matter: treat the whole file as body
    }

    private static string FirstHeading(string body) =>
        body.Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.StartsWith('#'))?.TrimStart('#').Trim() ?? "";

    private static string StripFirstHeading(string body, string name)
    {
        var lines = body.Split('\n');
        var first = lines[0].Trim();
        return first.StartsWith('#') && first.TrimStart('#').Trim() == name
            ? string.Join("\n", lines.Skip(1)).Trim()
            : body;
    }

    private static List<string> AsList(JsonNode? node) => node switch
    {
        JsonArray array => array.Select(n => n?.ToString() ?? "").Where(s => s.Length > 0).ToList(),
        null => [],
        _ => SplitCsv(node.ToString()),
    };

    private static List<string> SplitCsv(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? []
            : value.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries).ToList();

    private static bool AsBool(string? value, bool fallback) =>
        string.IsNullOrWhiteSpace(value) ? fallback : !FalseValues.Contains(value.Trim().ToLowerInvariant());
}
