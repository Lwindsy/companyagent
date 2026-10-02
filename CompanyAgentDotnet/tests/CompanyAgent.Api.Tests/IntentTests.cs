using CompanyAgent.Api.Intent;

namespace CompanyAgent.Api.Tests;

public class IntentTests
{
    [Theory]
    [InlineData(IntentCategory.TechnicalLogin, "technical_login")]
    [InlineData(IntentCategory.HumanHandoff, "human_handoff")]
    [InlineData(IntentCategory.Query, "query")]
    public void Intent_names_round_trip_as_snake_case(IntentCategory intent, string wire)
    {
        Assert.Equal(wire, intent.Name());
        Assert.Equal(intent, IntentNames.Parse(wire));
        Assert.Equal(intent, IntentNames.Parse(wire.ToUpperInvariant()));
    }

    [Fact]
    public void Unknown_intent_name_parses_as_other() => Assert.Equal(IntentCategory.Other, IntentNames.Parse("banana"));

    [Fact]
    public void Entities_are_found_inside_chinese_text()
    {
        var entities = EntityExtractor.Extract("订单号 #A12345 登录报401，多扣了 50 元，2026-09-30 发生");

        Assert.Contains("A12345", entities["order_id"]);
        Assert.Contains("401", entities["error_code"]);
        Assert.Contains("50 元", entities["amount"]);
        Assert.Contains("2026-09-30", entities["date"]);
    }

    [Fact]
    public async Task Llm_vote_decides_when_the_model_answers()
    {
        var llm = new ScriptedLlm().On("客服意图分析专家", """{"intent":"billing","confidence":0.95,"reasoning":"扣款问题"}""");
        var result = await new IntentRecognizer(llm).RecognizeAsync("为什么扣了两次款？", null);

        Assert.Equal(IntentCategory.Billing, result.Intent);
        Assert.Equal("billing", result.IntentGroup);
        Assert.True(result.Confidence >= 0.5);
        Assert.Equal("扣款问题", result.Reasoning);
    }

    [Fact]
    public async Task Specific_keyword_refines_a_generic_llm_label()
    {
        // The LLM says generic "billing" with modest confidence; the "退款" keyword refines it to "refund".
        var llm = new ScriptedLlm().On("客服意图分析专家", """{"intent":"billing","confidence":0.7}""");
        var result = await new IntentRecognizer(llm).RecognizeAsync("我想退款", null);

        Assert.Equal(IntentCategory.Refund, result.Intent);
        Assert.Equal("billing", result.IntentGroup);
        Assert.True(result.SourceScores.ContainsKey("refined_by_pattern"));
    }

    [Fact]
    public async Task Falls_back_to_ngram_similarity_when_the_llm_reply_is_not_json()
    {
        var result = await new IntentRecognizer(new ScriptedLlm("service unavailable")).RecognizeAsync("你好", null);

        Assert.Equal(IntentCategory.Greeting, result.Intent);
        Assert.Equal(0.0, result.SourceScores["llm"]);
    }

    [Fact]
    public async Task Urgent_wording_is_critical()
    {
        var result = await new IntentRecognizer(new ScriptedLlm()).RecognizeAsync("紧急！账户被盗了", null);
        Assert.Equal(UrgencyLevel.Critical, result.Urgency);
    }

    [Fact]
    public async Task Repeated_message_is_served_from_cache()
    {
        var llm = new ScriptedLlm().On("客服意图分析专家", """{"intent":"greeting","confidence":0.9}""");
        var recognizer = new IntentRecognizer(llm);

        await recognizer.RecognizeAsync("你好", null);
        await recognizer.RecognizeAsync("你好", null);

        Assert.Single(llm.Prompts);
    }
}
