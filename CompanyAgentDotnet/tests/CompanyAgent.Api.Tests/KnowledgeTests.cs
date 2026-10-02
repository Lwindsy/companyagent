using CompanyAgent.Api.Knowledge;
using CompanyAgent.Api.Tools;
using Microsoft.Extensions.Logging.Abstractions;

namespace CompanyAgent.Api.Tests;

public class KnowledgeTests
{
    private static KnowledgeBaseService CreateKnowledgeBase(Microsoft.Extensions.Options.IOptions<Configuration.CompanyAgentOptions>? options = null) =>
        new(options ?? TestOptions.Create(), NullLogger<KnowledgeBaseService>.Instance);

    [Fact]
    public void Seeds_default_documents_and_ranks_the_refund_policy_first()
    {
        var kb = CreateKnowledgeBase();
        var results = kb.Search("退款多久到账", 3);

        Assert.Equal(6, kb.DocCount);
        Assert.Equal("退款政策", results[0].Title);
        Assert.All(results, r => Assert.True(r.Score > 0));
    }

    [Fact]
    public void Added_documents_are_persisted_and_reloaded()
    {
        var options = TestOptions.Create();
        var kb = CreateKnowledgeBase(options);
        var versionBefore = kb.Version;

        kb.AddDocuments([("发票说明", "电子发票在订单完成后 24 小时内开具。")]);

        Assert.True(kb.Version > versionBefore);
        var reloaded = CreateKnowledgeBase(options);
        Assert.Equal(7, reloaded.DocCount);
        Assert.Equal("发票说明", reloaded.Search("电子发票什么时候开", 1)[0].Title);
    }

    [Fact]
    public void Re_adding_the_same_document_does_not_duplicate_chunks()
    {
        var kb = CreateKnowledgeBase();
        kb.AddDocuments([("A", "同样的内容")]);
        kb.AddDocuments([("A", "同样的内容")]);

        Assert.Equal(7, kb.DocCount);
    }

    [Fact]
    public void Splitter_respects_the_size_limit_and_overlaps_chunks()
    {
        var sentences = string.Concat(Enumerable.Range(0, 60).Select(i => $"这是第{i:00}句测试文本，用来验证切分。"));
        var chunks = new RecursiveTextSplitter(100, 20).Split(sentences);

        Assert.True(chunks.Count > 1);
        Assert.All(chunks, c => Assert.True(c.Length <= 100, $"chunk of {c.Length} chars"));
        // The tail of each chunk reappears at the start of the next one.
        Assert.Contains(chunks[0][^10..], chunks[1]);
    }

    [Fact]
    public void Stable_hash_is_deterministic_across_processes()
    {
        // FNV-1a reference value; string.GetHashCode() would differ on every run.
        Assert.Equal(0x4f9f2cabu, TextVectors.StableHash("hello"));
    }

    [Fact]
    public async Task Tool_falls_back_to_score_order_when_rerank_reply_is_invalid_and_caches_results()
    {
        var kb = CreateKnowledgeBase();
        var llm = new ScriptedLlm("not json");
        var tool = new KnowledgeToolManager(kb, llm);

        var result = await tool.SearchWithRewriteAsync("退款多久到账", 2);

        Assert.True(result.Reranked);
        Assert.Equal(2, result.Data.Count);
        Assert.Equal("退款政策", result.Data[0].Title);
        Assert.True(tool.Search("退款多久到账", 5).Cached);
    }

    [Fact]
    public async Task Tool_applies_the_llm_rerank_order()
    {
        var kb = CreateKnowledgeBase();
        var llm = new ScriptedLlm()
            .On("改写为 3 个不同角度", """["退款到账时间"]""")
            .On("按相关性排序", "[1,0]");
        var tool = new KnowledgeToolManager(kb, llm);

        var baseline = kb.Search("退款多久到账", 5);
        var result = await tool.SearchWithRewriteAsync("退款多久到账", 2);

        Assert.Equal(baseline[1].Id, result.Data[0].Id);
        Assert.Equal(baseline[0].Id, result.Data[1].Id);
    }

    [Fact]
    public void Adding_knowledge_invalidates_cached_search_results()
    {
        var kb = CreateKnowledgeBase();
        var tool = new KnowledgeToolManager(kb, new ScriptedLlm());
        tool.Search("积分", 3);

        kb.AddDocuments([("积分补充", "积分可以兑换优惠券。")]);

        Assert.False(tool.Search("积分", 3).Cached);
    }

    [Theory]
    [InlineData("", 3)]
    [InlineData("退款", 0)]
    [InlineData("退款", 51)]
    public async Task Invalid_arguments_are_rejected(string query, int topK)
    {
        var tool = new KnowledgeToolManager(CreateKnowledgeBase(), new ScriptedLlm());
        await Assert.ThrowsAsync<ArgumentException>(() => tool.SearchWithRewriteAsync(query, topK));
    }
}
