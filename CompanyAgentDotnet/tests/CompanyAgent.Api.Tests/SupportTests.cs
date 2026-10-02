using CompanyAgent.Api.Api;
using CompanyAgent.Api.Evaluation;
using CompanyAgent.Api.Llm;
using CompanyAgent.Api.Memory;
using CompanyAgent.Api.Skills;
using Microsoft.Extensions.Logging.Abstractions;

namespace CompanyAgent.Api.Tests;

public class SupportTests
{
    [Fact]
    public void Admin_session_expires_after_eight_hours()
    {
        var clock = new ManualTimeProvider(DateTimeOffset.Parse("2026-09-30T08:00:00Z"));
        var sessions = new AdminSessionService(TestOptions.Create(o => o.Admin.Password = "pw"), clock);

        var login = sessions.Login("admin", "pw");
        sessions.RequireSession($"Bearer {login.AccessToken}");

        clock.Now = clock.Now.AddHours(8);
        Assert.Throws<AdminUnauthorizedException>(() => sessions.RequireSession($"Bearer {login.AccessToken}"));
    }

    [Theory]
    [InlineData("admin", "wrong")]
    [InlineData("root", "pw")]
    public void Wrong_credentials_are_rejected(string username, string password)
    {
        var sessions = new AdminSessionService(TestOptions.Create(o => o.Admin.Password = "pw"), TimeProvider.System);
        Assert.Throws<AdminUnauthorizedException>(() => sessions.Login(username, password));
    }

    [Fact]
    public void Sign_in_is_disabled_without_a_configured_password()
    {
        var sessions = new AdminSessionService(TestOptions.Create(), TimeProvider.System);
        var error = Assert.Throws<AdminUnauthorizedException>(() => sessions.Login("admin", ""));
        Assert.Contains("ADMIN_PASSWORD", error.Message);
    }

    [Fact]
    public void Skill_front_matter_is_parsed_and_matched_by_keyword_and_agent()
    {
        var options = TestOptions.Create();
        var dir = Path.Combine(options.Value.Skills.RootDir, "billing");
        Directory.CreateDirectory(dir);
        File.WriteAllText(Path.Combine(dir, "SKILL.md"), """
            ---
            name: 退款规范
            description: 账单场景
            keywords: 退款, refund
            agents: billing
            ---

            # 退款规范

            先核验订单号。
            """);

        var manager = new SkillManager(options);

        var skill = Assert.Single(manager.Load());
        Assert.Equal("先核验订单号。", skill.Content);
        Assert.Contains("先核验订单号", manager.PromptFor("我要退款", "billing"));
        Assert.Equal("", manager.PromptFor("我要退款", "technical"));
        Assert.Equal("", manager.PromptFor("你好", "billing"));
    }

    [Fact]
    public void Macro_metrics_count_each_label()
    {
        var metrics = Metrics.PerClass(["a", "a", "b"], ["a", "b", "b"]);

        Assert.Equal(0.5, metrics["a"]["precision"]);
        Assert.Equal(1.0, metrics["a"]["recall"]);
        Assert.Equal(1.0, metrics["b"]["precision"]);
        Assert.Equal(0.5, metrics["b"]["recall"]);
    }

    [Fact]
    public async Task Working_memory_compresses_into_a_summary_after_the_threshold()
    {
        var options = TestOptions.Create(o => o.Memory.CompressAt = 6);
        var llm = new ScriptedLlm().On("总结以下对话", "用户在询问退款。");
        var memory = new MemoryManager(new InMemoryWorkingMemoryStore(), llm, options, NullLogger<MemoryManager>.Instance);

        for (var i = 0; i < 6; i++)
        {
            await memory.AddMessageAsync("u1", "c1", i % 2 == 0 ? MessageRole.User : MessageRole.Assistant, $"消息{i}");
        }

        var context = await memory.GetContextAsync("u1", "c1", "退款");
        Assert.Equal(5, context.RecentMessages.Count);
        Assert.Equal("消息1", context.RecentMessages[0].Content);
        Assert.Equal("用户在询问退款。", context.Summary);
        Assert.Contains("用户在询问退款。", context.RelevantHistory);
        Assert.True(File.Exists(options.Value.Storage.MemoryPath));
    }

    [Theory]
    [InlineData("claude-opus-5-5", true, false, true, true)]
    [InlineData("claude-sonnet-5-5", true, false, true, true)]
    [InlineData("claude-sonnet-4-6", false, true, true, false)]
    [InlineData("claude-haiku-4-5", false, true, false, false)]
    public void Request_shape_follows_the_model_generation(string model, bool alwaysThinks, bool temperature, bool effort, bool fallback)
    {
        var c = ClaudeModelCapabilities.For(model);
        Assert.Equal((alwaysThinks, temperature, effort, fallback), (c.AlwaysThinks, c.AcceptsTemperature, c.SupportsEffort, c.SupportsServerFallback));
    }

    [Fact]
    public void Fallback_reply_matches_the_topic()
    {
        Assert.Contains("退款", LlmFallback.Reply("我要退款"));
        Assert.Contains("错误码", LlmFallback.Reply("登录报错"));
    }
}
