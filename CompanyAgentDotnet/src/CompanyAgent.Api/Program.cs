using System.Text.Json;
using CompanyAgent.Api.Agents;
using CompanyAgent.Api.Api;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Evaluation;
using CompanyAgent.Api.Intent;
using CompanyAgent.Api.Knowledge;
using CompanyAgent.Api.Llm;
using CompanyAgent.Api.Memory;
using CompanyAgent.Api.Monitoring;
using CompanyAgent.Api.Skills;
using CompanyAgent.Api.Tools;
using Microsoft.OpenApi.Models;
using Prometheus;
using StackExchange.Redis;

var builder = WebApplication.CreateBuilder(args);

var port = builder.Configuration["SERVER_PORT"] ?? "8090";
builder.WebHost.UseUrls($"http://0.0.0.0:{port}");

var settings = CompanyAgentOptions.FromConfiguration(builder.Configuration);
builder.Services.AddSingleton(Microsoft.Extensions.Options.Options.Create(settings));
builder.Services.AddSingleton(TimeProvider.System);

// Outbound HTTP: DeepSeek gets the standard resilience handler (retry, timeout, circuit breaker).
builder.Services.AddHttpClient(LlmGateway.DeepSeekHttpClient).AddStandardResilienceHandler(o =>
{
    o.AttemptTimeout.Timeout = TimeSpan.FromSeconds(60);
    o.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(150);
    o.CircuitBreaker.SamplingDuration = TimeSpan.FromSeconds(120);
});
builder.Services.AddHttpClient(PerformanceMonitor.WebhookHttpClient, c => c.Timeout = TimeSpan.FromSeconds(5));

// Working memory: Redis unless explicitly disabled for local runs.
if (bool.TryParse(builder.Configuration["REDIS_ENABLED"], out var redisEnabled) && !redisEnabled)
{
    builder.Services.AddSingleton<IWorkingMemoryStore, InMemoryWorkingMemoryStore>();
}
else
{
    builder.Services.AddSingleton<IConnectionMultiplexer>(_ =>
    {
        var redis = new ConfigurationOptions
        {
            EndPoints = { { settings.Redis.Host, settings.Redis.Port } },
            Password = string.IsNullOrEmpty(settings.Redis.Password) ? null : settings.Redis.Password,
            AbortOnConnectFail = false, // start even if Redis is down; memory calls degrade with a warning
            ConnectTimeout = 2000,
            SyncTimeout = 2000,
        };
        return ConnectionMultiplexer.Connect(redis);
    });
    builder.Services.AddSingleton<IWorkingMemoryStore, RedisWorkingMemoryStore>();
}

builder.Services.AddSingleton<ILlmGateway, LlmGateway>();
builder.Services.AddSingleton<SkillManager>();
builder.Services.AddSingleton<IntentRecognizer>();
builder.Services.AddSingleton<BaseAgent, GeneralAgent>();
builder.Services.AddSingleton<BaseAgent, TechnicalAgent>();
builder.Services.AddSingleton<BaseAgent, BillingAgent>();
builder.Services.AddSingleton<AgentPool>();
builder.Services.AddSingleton<AgentOrchestrator>();
builder.Services.AddSingleton<AnswerVerifier>();
builder.Services.AddSingleton<KnowledgeBaseService>();
builder.Services.AddSingleton<KnowledgeToolManager>();
builder.Services.AddSingleton<MemoryManager>();
builder.Services.AddSingleton<ProfileUpdateQueue>();
builder.Services.AddSingleton<LlmJudge>();
builder.Services.AddSingleton<EndToEndEvaluator>();
builder.Services.AddSingleton<PerformanceMonitor>();
builder.Services.AddSingleton<AdminSessionService>();
builder.Services.AddHostedService<ProfileUpdateWorker>();
builder.Services.AddHostedService<MonitorWorker>();

builder.Services.AddExceptionHandler<ApiExceptionHandler>();
builder.Services.AddProblemDetails();
builder.Services.AddControllers().AddJsonOptions(o =>
{
    // snake_case on the wire, matching the Python and Java backends.
    o.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
    o.JsonSerializerOptions.DictionaryKeyPolicy = null;
    o.JsonSerializerOptions.Encoder = JsonText.Options.Encoder;
});

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(o =>
{
    o.SwaggerDoc("v1", new OpenApiInfo
    {
        Title = "CompanyAgent .NET API",
        Version = "0.1.0",
        Description = "CompanyAgent customer-support agent API (ASP.NET Core): chat, knowledge base, monitoring and evaluation.",
    });
    o.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
    {
        Type = SecuritySchemeType.Http,
        Scheme = "bearer",
        Description = "Token from POST /admin/login",
    });
    o.AddSecurityRequirement(new OpenApiSecurityRequirement
    {
        [new OpenApiSecurityScheme { Reference = new OpenApiReference { Type = ReferenceType.SecurityScheme, Id = "Bearer" } }] = [],
    });
    var xml = Path.Combine(AppContext.BaseDirectory, "CompanyAgent.Api.xml");
    if (File.Exists(xml)) o.IncludeXmlComments(xml);
});

var app = builder.Build();

app.UseExceptionHandler();
app.UseHttpMetrics();

// Behind Caddy/Nginx the service is mounted under /api/dotnet with the prefix stripped;
// X-Forwarded-Prefix lets Swagger "Try it out" call the right public URL.
app.UseSwagger(o =>
{
    o.RouteTemplate = "v3/api-docs/{documentName}.json";
    o.PreSerializeFilters.Add((doc, request) =>
    {
        var prefix = request.Headers["X-Forwarded-Prefix"].FirstOrDefault() ?? "";
        doc.Servers = [new OpenApiServer { Url = prefix.Length > 0 ? prefix : "/" }];
    });
});
// Swashbuckle's own /docs redirect is absolute and would drop the proxy prefix; a relative one keeps it.
app.Use(async (context, next) =>
{
    if (context.Request.Path.Equals("/docs", StringComparison.OrdinalIgnoreCase))
    {
        context.Response.Redirect("docs/index.html");
        return;
    }
    await next();
});
app.UseSwaggerUI(o =>
{
    o.RoutePrefix = "docs";
    o.SwaggerEndpoint("../v3/api-docs/v1.json", "CompanyAgent .NET API");
    o.DocumentTitle = "CompanyAgent .NET API Docs";
});

app.MapControllers();
app.MapMetrics("/metrics");

// Build the knowledge base and load persisted memory at startup rather than on the first request.
app.Services.GetRequiredService<KnowledgeBaseService>();
app.Services.GetRequiredService<MemoryManager>();
if (!settings.Admin.Configured)
{
    app.Logger.LogWarning("ADMIN_PASSWORD is not set; administrator sign-in is disabled");
}

app.Run();

public partial class Program;
