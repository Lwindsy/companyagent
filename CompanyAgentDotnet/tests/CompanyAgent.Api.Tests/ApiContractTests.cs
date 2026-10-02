using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using CompanyAgent.Api.Llm;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace CompanyAgent.Api.Tests;

/// <summary>Runs the real ASP.NET Core pipeline in memory, with a stub LLM and no Redis.</summary>
public class ApiContractTests : IClassFixture<ApiContractTests.Factory>
{
    private readonly HttpClient _client;

    public ApiContractTests(Factory factory) =>
        _client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });

    public sealed class Factory : WebApplicationFactory<Program>
    {
        private readonly string _dataDir = Path.Combine(Path.GetTempPath(), "companyagent-api-tests", Guid.NewGuid().ToString("N"));

        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.UseSetting("REDIS_ENABLED", "false");
            builder.UseSetting("ADMIN_PASSWORD", "test-pw");
            builder.UseSetting("COMPANYAGENT_DATA_DIR", _dataDir);
            builder.UseSetting("EVAL_BASELINE_PATH", Path.Combine(_dataDir, "baseline.json"));
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<ILlmGateway>();
                services.AddSingleton<ILlmGateway>(new ScriptedLlm("您好，这是测试回复。")
                    .On("客服意图分析专家", """{"intent":"refund","confidence":0.9,"reasoning":"退款"}""")
                    .On("回答质量校验器", """{"pass":true,"grounded":true,"need_escalation":false,"reason":"ok"}"""));
            });
        }
    }

    [Fact]
    public async Task Chat_returns_the_snake_case_contract_used_by_the_frontend()
    {
        var response = await _client.PostAsJsonAsync("/chat", new { message = "我想申请退款", user_id = "u1", conversation_id = "conv-42" });
        response.EnsureSuccessStatusCode();
        var body = (await response.Content.ReadFromJsonAsync<JsonObject>())!;

        Assert.Equal("conv-42", body["conv_id"]!.GetValue<string>());
        Assert.Equal("refund", body["intent"]!.GetValue<string>());
        Assert.Equal("billing", body["intent_group"]!.GetValue<string>());
        Assert.Equal("billing", body["agent_type"]!.GetValue<string>());
        Assert.True(body["knowledge_used"]!.GetValue<bool>());
        Assert.True(body["verified"]!.GetValue<bool>());
        Assert.NotNull(body["latency_ms"]);
        Assert.NotNull(body["intent_source_scores"]!["llm"]);
    }

    [Fact]
    public async Task Chat_generates_a_conversation_id_when_missing()
    {
        var body = await (await _client.PostAsJsonAsync("/chat", new { message = "你好" })).Content.ReadFromJsonAsync<JsonObject>();
        Assert.True(Guid.TryParse(body!["conv_id"]!.GetValue<string>(), out _));
    }

    [Fact]
    public async Task Empty_message_is_a_validation_error()
    {
        var response = await _client.PostAsJsonAsync("/chat", new { message = "" });
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Theory]
    [InlineData("GET", "/monitor")]
    [InlineData("GET", "/knowledge/stats")]
    [InlineData("POST", "/search?query=x")]
    [InlineData("POST", "/eval/run")]
    public async Task Admin_endpoints_require_a_token(string method, string path)
    {
        var response = await _client.SendAsync(new HttpRequestMessage(new HttpMethod(method), path));
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Admin_can_add_and_search_knowledge()
    {
        var login = await (await _client.PostAsJsonAsync("/admin/login", new { username = "admin", password = "test-pw" }))
            .Content.ReadFromJsonAsync<JsonObject>();
        var token = login!["access_token"]!.GetValue<string>();
        Assert.Equal(28800, login["expires_in"]!.GetValue<long>());

        var add = new HttpRequestMessage(HttpMethod.Post, "/knowledge/add")
        {
            Content = JsonContent.Create(new { documents = new[] { new { title = "发票说明", content = "电子发票在订单完成后 24 小时内开具。" } } }),
        };
        add.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        var added = await (await _client.SendAsync(add)).Content.ReadFromJsonAsync<JsonObject>();
        Assert.Equal(1, added!["added_chunks"]!.GetValue<int>());

        var search = new HttpRequestMessage(HttpMethod.Post, "/search?query=" + Uri.EscapeDataString("电子发票") + "&top_k=1");
        search.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        var found = await (await _client.SendAsync(search)).Content.ReadFromJsonAsync<JsonObject>();
        Assert.Equal("发票说明", found!["results"]![0]!["title"]!.GetValue<string>());
    }

    [Fact]
    public async Task Metrics_and_swagger_are_exposed()
    {
        Assert.Contains("companyagent_monitor_collections_total", await _client.GetStringAsync("/metrics"));
        var docs = await _client.GetAsync("/docs");
        Assert.Equal(HttpStatusCode.Redirect, docs.StatusCode);
        Assert.Equal("docs/index.html", docs.Headers.Location!.OriginalString);
        Assert.Contains("\"/chat\"", await _client.GetStringAsync("/v3/api-docs/v1.json"));
    }
}
