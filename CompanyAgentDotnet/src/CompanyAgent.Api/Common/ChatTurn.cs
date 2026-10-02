namespace CompanyAgent.Api.Common;

/// <summary>One prior turn passed to intent recognition and agents ("user" or "assistant").</summary>
public sealed record ChatTurn(string Role, string Content)
{
    public override string ToString() => $"{Role}: {Content}";
}
