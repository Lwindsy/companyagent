using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using CompanyAgent.Api.Configuration;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Api;

public sealed record AdminLoginResult(string AccessToken, long ExpiresIn, string Username);

public sealed class AdminUnauthorizedException(string message) : Exception(message);

/// <summary>In-memory bearer sessions for the admin workspace (8-hour TTL).</summary>
public sealed class AdminSessionService
{
    private static readonly TimeSpan SessionTtl = TimeSpan.FromHours(8);

    private readonly AdminOptions _options;
    private readonly TimeProvider _time;
    private readonly ConcurrentDictionary<string, DateTimeOffset> _sessions = new();

    public AdminSessionService(IOptions<CompanyAgentOptions> options, TimeProvider time)
    {
        _options = options.Value.Admin;
        _time = time;
    }

    public AdminLoginResult Login(string username, string password)
    {
        if (!_options.Configured)
        {
            throw new AdminUnauthorizedException("Administrator sign-in is disabled: ADMIN_PASSWORD is not configured");
        }
        if (!FixedTimeEquals(username, _options.Username) || !FixedTimeEquals(password, _options.Password))
        {
            throw new AdminUnauthorizedException("Invalid administrator credentials");
        }
        var token = Base64UrlToken(RandomNumberGenerator.GetBytes(32));
        var now = _time.GetUtcNow();
        _sessions[token] = now + SessionTtl;
        PurgeExpired(now);
        return new AdminLoginResult(token, (long)SessionTtl.TotalSeconds, _options.Username);
    }

    public void RequireSession(string? authorization)
    {
        if (authorization is null || !authorization.StartsWith("Bearer ", StringComparison.Ordinal))
        {
            throw new AdminUnauthorizedException("Administrator authentication is required");
        }
        var token = authorization["Bearer ".Length..].Trim();
        if (!_sessions.TryGetValue(token, out var expiresAt) || _time.GetUtcNow() >= expiresAt)
        {
            _sessions.TryRemove(token, out _);
            throw new AdminUnauthorizedException("Administrator session has expired");
        }
    }

    private void PurgeExpired(DateTimeOffset now)
    {
        foreach (var (token, expiresAt) in _sessions)
        {
            if (expiresAt <= now) _sessions.TryRemove(token, out _);
        }
    }

    // Constant-time comparison so response timing does not leak how much of a credential matched.
    private static bool FixedTimeEquals(string? left, string right) =>
        CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(left ?? ""), Encoding.UTF8.GetBytes(right));

    private static string Base64UrlToken(byte[] bytes) =>
        Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}

/// <summary>Requires a valid admin bearer token on the decorated action.</summary>
[AttributeUsage(AttributeTargets.Method | AttributeTargets.Class)]
public sealed class AdminOnlyAttribute : Attribute, IAuthorizationFilter
{
    public void OnAuthorization(AuthorizationFilterContext context)
    {
        var sessions = context.HttpContext.RequestServices.GetRequiredService<AdminSessionService>();
        sessions.RequireSession(context.HttpContext.Request.Headers.Authorization.FirstOrDefault());
    }
}
