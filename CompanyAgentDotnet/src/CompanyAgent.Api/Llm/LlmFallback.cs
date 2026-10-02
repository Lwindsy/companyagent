namespace CompanyAgent.Api.Llm;

/// <summary>Deterministic replies used when the model is unavailable (same texts as the Java backend).</summary>
public static class LlmFallback
{
    public static string Reply(string? userPrompt)
    {
        var prompt = userPrompt ?? "";
        var lower = prompt.ToLowerInvariant();
        if (prompt.Contains("退款") || lower.Contains("refund"))
        {
            return "根据当前知识库，退款通常需要先提交申请并等待审核。请提供订单号，我可以继续帮你判断是否需要转人工审核。";
        }
        if (prompt.Contains("报错") || prompt.Contains("登录") || lower.Contains("error"))
        {
            return "我建议先确认账号状态、网络环境和错误码。如果问题持续，请提供错误码和发生时间，技术支持会进一步排查。";
        }
        if (prompt.Contains("扣款") || prompt.Contains("账单") || prompt.Contains("发票"))
        {
            return "账单问题需要核对支付记录、订单号和扣款时间。请提供相关信息，涉及退款或发票开具时会进入人工审核。";
        }
        return "我已收到你的问题。当前模型服务不可用，系统返回了本地降级回复；请稍后重试或转人工处理。";
    }
}
