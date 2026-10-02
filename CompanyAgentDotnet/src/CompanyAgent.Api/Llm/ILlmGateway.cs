namespace CompanyAgent.Api.Llm;

/// <summary>Single entry point for every model call made by agents, intent recognition, RAG and evaluation.</summary>
public interface ILlmGateway
{
    Task<string> ChatAsync(string systemPrompt, string userPrompt, double temperature, int maxTokens,
        CancellationToken cancellationToken = default);
}
