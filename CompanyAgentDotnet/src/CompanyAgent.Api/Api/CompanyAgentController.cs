using System.Text;
using System.Text.Json.Nodes;
using CompanyAgent.Api.Agents;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Evaluation;
using CompanyAgent.Api.Intent;
using CompanyAgent.Api.Knowledge;
using CompanyAgent.Api.Memory;
using CompanyAgent.Api.Monitoring;
using CompanyAgent.Api.Skills;
using CompanyAgent.Api.Tools;
using Microsoft.AspNetCore.Mvc;

namespace CompanyAgent.Api.Api;

/// <summary>Customer-support chat, knowledge base, monitoring and evaluation endpoints.</summary>
[ApiController]
[Produces("application/json")]
public sealed class CompanyAgentController(
    AgentOrchestrator orchestrator,
    IntentRecognizer intentRecognizer,
    MemoryManager memory,
    ProfileUpdateQueue profileUpdates,
    KnowledgeToolManager knowledgeTool,
    KnowledgeBaseService knowledgeBase,
    AnswerVerifier verifier,
    PerformanceMonitor monitor,
    EndToEndEvaluator evaluator,
    SkillManager skills,
    AdminSessionService adminSessions) : ControllerBase
{
    private const long MaxUploadBytes = 10L * 1024 * 1024;

    private static readonly HashSet<IntentCategory> KnowledgeIntents =
    [
        IntentCategory.Query, IntentCategory.Complaint, IntentCategory.Request, IntentCategory.Technical,
        IntentCategory.Billing, IntentCategory.Account, IntentCategory.OrderStatus, IntentCategory.Logistics,
        IntentCategory.Refund, IntentCategory.Invoice, IntentCategory.PaymentIssue, IntentCategory.AccountSecurity,
        IntentCategory.TechnicalLogin, IntentCategory.TechnicalCrash,
    ];

    /// <summary>Health check with agent routing statistics.</summary>
    [HttpGet("/health")]
    public object Health() => new Dictionary<string, object> { ["status"] = "ok", ["agents"] = orchestrator.Stats() };

    /// <summary>Administrator sign in.</summary>
    [HttpPost("/admin/login")]
    public AdminLoginResult AdminLogin([FromBody] AdminLoginRequest request) =>
        adminSessions.Login(request.Username, request.Password);

    /// <summary>Administrator workspace overview.</summary>
    [AdminOnly]
    [HttpGet("/admin/overview")]
    public object AdminOverview() => new Dictionary<string, object>
    {
        ["status"] = "ok",
        ["agent_stats"] = orchestrator.Stats(),
        ["knowledge_chunks"] = knowledgeBase.DocCount,
        ["alerts"] = monitor.ActiveAlerts(),
    };

    /// <summary>
    /// Full chat flow: memory lookup, knowledge retrieval, intent recognition, multi-agent routing,
    /// answer verification and memory write-back.
    /// </summary>
    [HttpPost("/chat")]
    public async Task<ChatResponse> Chat([FromBody] ChatRequest request, CancellationToken cancellationToken)
    {
        var userId = request.UserIdOrDefault;
        var conversationId = string.IsNullOrWhiteSpace(request.ConversationId) ? Guid.NewGuid().ToString() : request.ConversationId;

        var memoryContext = await memory.GetContextAsync(userId, conversationId, request.Message);
        var history = memoryContext.RecentMessages.TakeLast(5).Select(m => new ChatTurn(m.RoleName, m.Content)).ToList();
        var intent = await intentRecognizer.RecognizeAsync(request.Message, history, cancellationToken);
        var knowledge = KnowledgeIntents.Contains(intent.Intent)
            ? await knowledgeTool.SearchWithRewriteAsync(request.Message, 3, cancellationToken)
            : new ToolResult<List<SearchResult>>(true, [], KnowledgeToolManager.ToolName, null, false, 0, false);
        var fullContext = Join(memoryContext.ToPromptText(), BuildKnowledgeContext(knowledge.Data));

        var result = await orchestrator.RunAsync(
            AgentRequest.Create(request.Message, userId, conversationId, fullContext, history, intent), cancellationToken);
        var verification = await verifier.VerifyAsync(request.Message, result.Response, fullContext, cancellationToken);

        await memory.AddMessageAsync(userId, conversationId, MessageRole.User, request.Message, cancellationToken);
        await memory.AddMessageAsync(userId, conversationId, MessageRole.Assistant, result.Response, cancellationToken);
        profileUpdates.Enqueue(userId, conversationId);

        return new ChatResponse(
            conversationId,
            result.Response,
            result.Intent?.Name() ?? "other",
            intent.IntentGroup,
            result.AgentType.Name(),
            result.AgentTypes.Select(a => a.Name()).ToList(),
            result.PrimaryAgent.Name(),
            result.SupportingAgents.Select(a => a.Name()).ToList(),
            result.RoutingReason,
            result.RoutingConfidence,
            result.Escalated || verification.NeedEscalation,
            result.LatencyMs,
            knowledge.Success && knowledge.Data.Count > 0,
            verification.Pass,
            verification.Grounded,
            intent.Entities,
            JsonText.Round(intent.Confidence, 4),
            intent.SourceScores);
    }

    /// <summary>Knowledge search with query rewriting, parallel recall and LLM rerank.</summary>
    /// <param name="query">Search text or user question.</param>
    /// <param name="topK">Number of results (the <c>topK</c> query name is also accepted).</param>
    /// <param name="topKCamel">Alias of <c>top_k</c>.</param>
    /// <param name="cancellationToken">Request abort token.</param>
    [AdminOnly]
    [HttpPost("/search")]
    public async Task<object> Search([FromQuery] string query, [FromQuery(Name = "top_k")] int? topK,
        [FromQuery(Name = "topK")] int? topKCamel, CancellationToken cancellationToken)
    {
        var result = await knowledgeTool.SearchWithRewriteAsync(query, topK ?? topKCamel ?? 5, cancellationToken);
        return new Dictionary<string, object> { ["query"] = query, ["results"] = result.Data, ["reranked"] = result.Reranked };
    }

    /// <summary>Add documents to the knowledge base; they are chunked and persisted.</summary>
    [AdminOnly]
    [HttpPost("/knowledge/add")]
    public object AddKnowledge([FromBody] BatchDocInput input)
    {
        var added = knowledgeBase.AddDocuments(input.Documents.Select(d => (d.Title, d.Content)));
        return new Dictionary<string, object> { ["added_chunks"] = added, ["total_chunks"] = knowledgeBase.DocCount };
    }

    /// <summary>Upload a .txt, .md or .json file (max 10 MB). JSON must be an array of {"title","content"}.</summary>
    [AdminOnly]
    [HttpPost("/knowledge/upload")]
    [Consumes("multipart/form-data")]
    [RequestSizeLimit(MaxUploadBytes + 64 * 1024)]
    public async Task<object> UploadKnowledge(IFormFile file, CancellationToken cancellationToken)
    {
        if (file.Length > MaxUploadBytes) throw new ArgumentException("文件大小超过 10MB 限制");
        var filename = string.IsNullOrWhiteSpace(file.FileName) ? "unknown" : Path.GetFileName(file.FileName);
        string text;
        using (var reader = new StreamReader(file.OpenReadStream(), Encoding.UTF8))
        {
            text = await reader.ReadToEndAsync(cancellationToken);
        }
        var docs = filename.EndsWith(".json", StringComparison.OrdinalIgnoreCase)
            ? ParseJsonDocs(text)
            : [(Path.GetFileNameWithoutExtension(filename), text)];
        var added = knowledgeBase.AddDocuments(docs);
        return new Dictionary<string, object>
        {
            ["message"] = $"文件 {filename} 导入成功",
            ["added_chunks"] = added,
            ["total_chunks"] = knowledgeBase.DocCount,
        };
    }

    /// <summary>Number of knowledge chunks.</summary>
    [AdminOnly]
    [HttpGet("/knowledge/stats")]
    public object KnowledgeStats() => new Dictionary<string, object> { ["total_chunks"] = knowledgeBase.DocCount };

    /// <summary>Agent metrics, tool statistics, alerts and suggestions.</summary>
    [AdminOnly]
    [HttpGet("/monitor")]
    public object Monitor() => monitor.Summary();

    /// <summary>Currently loaded Skills and load errors.</summary>
    [AdminOnly]
    [HttpGet("/skills")]
    public object Skills() => skills.Summary();

    /// <summary>Rescan the Skills directory without restarting.</summary>
    [AdminOnly]
    [HttpPost("/skills/reload")]
    public object ReloadSkills()
    {
        skills.Load();
        return skills.Summary();
    }

    /// <summary>Run intent and dialog-quality evaluation; an empty body uses the built-in cases.</summary>
    [AdminOnly]
    [HttpPost("/eval/run")]
    public Task<Dictionary<string, object>> Eval([FromBody(EmptyBodyBehavior = Microsoft.AspNetCore.Mvc.ModelBinding.EmptyBodyBehavior.Allow)] EvalRunRequest? request, CancellationToken cancellationToken) =>
        evaluator.RunAsync(request, cancellationToken);

    private static string BuildKnowledgeContext(IReadOnlyList<SearchResult> results)
    {
        if (results.Count == 0) return "";
        var parts = new List<string> { "[知识库检索结果]" };
        parts.AddRange(results.Select((item, i) => $"{i + 1}. 标题: {item.Title}\n   相关度: {item.Score}\n   内容: {item.Content}"));
        parts.Add("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。");
        return string.Join("\n", parts);
    }

    private static string Join(string left, string right)
    {
        if (string.IsNullOrWhiteSpace(left)) return right;
        return string.IsNullOrWhiteSpace(right) ? left : left + "\n\n" + right;
    }

    private static List<(string Title, string Content)> ParseJsonDocs(string text)
    {
        if (JsonNode.Parse(text) is not JsonArray array) throw new ArgumentException("JSON 文件必须是数组");
        return array.OfType<JsonObject>()
            .Select(o => (o["title"]?.ToString() ?? "未命名文档", o["content"]?.ToString() ?? ""))
            .ToList();
    }
}
