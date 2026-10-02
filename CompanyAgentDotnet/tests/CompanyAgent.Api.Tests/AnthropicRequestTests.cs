using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Anthropic;
using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Llm;

namespace CompanyAgent.Api.Tests;

/// <summary>Captures the JSON the SDK would send, without calling the API.</summary>
public class AnthropicRequestTests
{
    private sealed class CaptureHandler : HttpMessageHandler
    {
        public JsonObject? Body { get; private set; }
        public HttpRequestMessage? Request { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Request = request;
            Body = JsonNode.Parse(await request.Content!.ReadAsStringAsync(cancellationToken)) as JsonObject;
            const string reply = """
                {"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5-5",
                 "content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","stop_sequence":null,
                 "usage":{"input_tokens":1,"output_tokens":1}}
                """;
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(reply, Encoding.UTF8, "application/json") };
        }
    }

    private static async Task<CaptureHandler> SendAsync(LlmOptions options, string system = "sys")
    {
        var handler = new CaptureHandler();
        var client = new AnthropicClient { ApiKey = "test-key", HttpClient = new HttpClient(handler) };
        var request = LlmGateway.BuildAnthropicRequest(options, system, "hello", 0.2, 256);
        await client.Beta.Messages.Create(request);
        return handler;
    }

    [Fact]
    public async Task Current_model_request_uses_effort_fallbacks_and_no_temperature()
    {
        var handler = await SendAsync(new LlmOptions { AnthropicModel = "claude-opus-5-5", AnthropicEffort = "low" });
        var body = handler.Body!;

        Assert.Equal("claude-opus-5-5", body["model"]!.GetValue<string>());
        Assert.Equal(4096, body["max_tokens"]!.GetValue<int>());
        Assert.Null(body["temperature"]);
        Assert.Equal("low", body["output_config"]!["effort"]!.GetValue<string>());
        Assert.Equal("default", body["fallbacks"]!.GetValue<string>());
        Assert.Equal("sys", body["system"]!.GetValue<string>());
        Assert.Contains("server-side-fallback-2026-07-01", handler.Request!.Headers.GetValues("anthropic-beta"));
    }

    [Fact]
    public async Task Older_model_keeps_temperature_and_requested_max_tokens()
    {
        var body = (await SendAsync(new LlmOptions { AnthropicModel = "claude-haiku-4-5" }, system: "")).Body!;

        Assert.Equal(256, body["max_tokens"]!.GetValue<int>());
        Assert.Equal(0.2, body["temperature"]!.GetValue<double>(), 3);
        Assert.Null(body["output_config"]);
        Assert.Null(body["fallbacks"]);
        Assert.Null(body["system"]);
    }

    [Fact]
    public async Task Fallbacks_are_not_sent_through_a_proxy_base_url()
    {
        var body = (await SendAsync(new LlmOptions { AnthropicModel = "claude-opus-5-5", AnthropicBaseUrl = "https://proxy.example.com" })).Body!;
        Assert.Null(body["fallbacks"]);
    }
}
