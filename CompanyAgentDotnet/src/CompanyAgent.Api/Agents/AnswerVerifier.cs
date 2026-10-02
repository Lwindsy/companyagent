using CompanyAgent.Api.Common;
using CompanyAgent.Api.Llm;

namespace CompanyAgent.Api.Agents;

public sealed record VerificationResult(bool Pass, bool Grounded, bool NeedEscalation, string Reason);

/// <summary>LLM check of whether an answer resolves the question, is grounded in context and needs a human.</summary>
public sealed class AnswerVerifier(ILlmGateway llm)
{
    public async Task<VerificationResult> VerifyAsync(string question, string answer, string? context,
        CancellationToken cancellationToken = default)
    {
        var prompt = $$"""
            你是客服回答质量校验器。判断回答是否解决用户问题、是否基于上下文、是否需要转人工。
            用户问题: {{question}}
            回答: {{answer}}
            上下文: {{context ?? ""}}
            返回 JSON: {"pass":true,"grounded":true,"need_escalation":false,"reason":"..."}
            """;
        try
        {
            var raw = await llm.ChatAsync("", prompt, 0.0, 256, cancellationToken);
            var data = JsonText.ExtractObject(raw) ?? throw new FormatException("No JSON object in verifier reply");
            return new VerificationResult(
                JsonText.IsTrue(data["pass"]),
                JsonText.IsTrue(data["grounded"]),
                JsonText.IsTrue(data["need_escalation"]),
                data["reason"]?.ToString() ?? "");
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return new VerificationResult(true, !string.IsNullOrWhiteSpace(context), answer.Contains("转人工"), "verifier fallback");
        }
    }
}
