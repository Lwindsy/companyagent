using System.Diagnostics;
using System.Text;
using CompanyAgent.Api.Llm;
using CompanyAgent.Api.Skills;

namespace CompanyAgent.Api.Agents;

/// <summary>Shared agent behaviour: prompt assembly, Skill injection, stats and escalation detection.</summary>
public abstract class BaseAgent
{
    private static readonly string[] EscalationMarkers = ["转人工", "人工客服", "escalate", "specialist", "无法处理"];

    private readonly ILlmGateway _llm;
    private readonly SkillManager _skills;

    protected BaseAgent(ILlmGateway llm, SkillManager skills)
    {
        _llm = llm;
        _skills = skills;
    }

    public abstract AgentType Type { get; }

    protected abstract string SystemPrompt { get; }

    public AgentStats Stats { get; } = new();

    public async Task<AgentResponse> HandleAsync(AgentRequest request, CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        try
        {
            var content = await _llm.ChatAsync(BuildSystemPrompt(request), BuildPrompt(request), 0.2, 1024, cancellationToken);
            Stats.Record(true, stopwatch.ElapsedMilliseconds);
            return new AgentResponse(Type, content, true, 1.0, stopwatch.ElapsedMilliseconds, NeedsEscalation(content));
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Stats.Record(false, stopwatch.ElapsedMilliseconds);
            return new AgentResponse(Type, "抱歉，处理您的请求时出现问题，请稍后重试。", false, 0.0, stopwatch.ElapsedMilliseconds, false);
        }
    }

    private static string BuildPrompt(AgentRequest request)
    {
        var prompt = new StringBuilder();
        if (!string.IsNullOrWhiteSpace(request.Context))
        {
            prompt.Append("[背景信息]\n").Append(request.Context).Append("\n\n");
        }
        var entities = request.Entities.Where(kv => kv.Value.Count > 0).ToList();
        if (entities.Count > 0)
        {
            prompt.Append("[结构化实体]\n")
                .Append(string.Join(", ", entities.Select(kv => $"{kv.Key}=[{string.Join(", ", kv.Value)}]")))
                .Append("\n\n");
        }
        prompt.Append("[用户问题]\n").Append(request.Message);
        return prompt.ToString();
    }

    private string BuildSystemPrompt(AgentRequest request)
    {
        var skillPrompt = _skills.PromptFor(request.Message, Type.Name());
        return skillPrompt.Length == 0 ? SystemPrompt : SystemPrompt + "\n\n[动态 Skills]\n" + skillPrompt;
    }

    private static bool NeedsEscalation(string? content)
    {
        var text = (content ?? "").ToLowerInvariant();
        return EscalationMarkers.Any(text.Contains);
    }
}

public sealed class GeneralAgent(ILlmGateway llm, SkillManager skills) : BaseAgent(llm, skills)
{
    public override AgentType Type => AgentType.General;

    protected override string SystemPrompt =>
        "你是 CompanyAgent 智能客服。友好、简洁地回答用户问题。如果问题超出能力范围，说明原因并建议转接专业客服。";
}

public sealed class TechnicalAgent(ILlmGateway llm, SkillManager skills) : BaseAgent(llm, skills)
{
    public override AgentType Type => AgentType.Technical;

    protected override string SystemPrompt =>
        "你是技术支持专家。专注故障排查、错误诊断、系统配置。提供步骤化方案，遇到后台操作明确升级处理。";
}

public sealed class BillingAgent(ILlmGateway llm, SkillManager skills) : BaseAgent(llm, skills)
{
    public override AgentType Type => AgentType.Billing;

    protected override string SystemPrompt =>
        "你是账单服务专家。专注账单查询、退款申请、发票问题、订阅管理。涉及实际退款操作时说明需要人工审核。";
}
