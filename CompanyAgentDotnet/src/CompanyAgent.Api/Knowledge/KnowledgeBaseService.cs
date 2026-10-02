using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Configuration;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Knowledge;

public sealed record SearchResult(
    string Id,
    string Title,
    string Content,
    double Score,
    int Chunk,
    IReadOnlyDictionary<string, object> Metadata);

public sealed record KnowledgeDocument(string Id, string Title, string Content, int ChunkIndex,
    Dictionary<string, object> Metadata);

/// <summary>
/// Chunked knowledge base persisted as JSON. Search fuses normalised BM25 and hashed-vector cosine scores.
/// Documents are held in an immutable snapshot that is swapped on write, so searches never take a lock.
/// </summary>
public sealed class KnowledgeBaseService
{
    private const double K1 = 1.5;
    private const double B = 0.75;

    private readonly RagOptions _rag;
    private readonly string _storePath;
    private readonly ILogger<KnowledgeBaseService> _logger;
    private readonly RecursiveTextSplitter _splitter = new(500, 80);
    private readonly object _writeLock = new();
    private volatile Snapshot _snapshot = Snapshot.Empty;
    private long _version;

    public KnowledgeBaseService(IOptions<CompanyAgentOptions> options, ILogger<KnowledgeBaseService> logger)
    {
        _rag = options.Value.Rag;
        _storePath = options.Value.Storage.KnowledgePath;
        _logger = logger;
        LoadPersisted();
        if (_snapshot.Docs.Count == 0)
        {
            AddDocuments(DefaultDocuments());
        }
    }

    public int DocCount => _snapshot.Docs.Count;

    /// <summary>Increments on every write; callers use it to invalidate cached search results.</summary>
    public long Version => Interlocked.Read(ref _version);

    public int AddDocuments(IEnumerable<(string Title, string Content)> inputs)
    {
        lock (_writeLock)
        {
            var docs = _snapshot.Docs.ToDictionary(d => d.Doc.Id);
            var order = _snapshot.Docs.Select(d => d.Doc.Id).ToList();
            var added = 0;
            foreach (var (rawTitle, content) in inputs)
            {
                var title = string.IsNullOrWhiteSpace(rawTitle) ? "未命名文档" : rawTitle;
                var chunks = _splitter.Split(content);
                for (var i = 0; i < chunks.Count; i++)
                {
                    var text = chunks[i];
                    var id = Md5($"{title}_{i}_{text[..Math.Min(50, text.Length)]}");
                    var metadata = new Dictionary<string, object>
                    {
                        ["title"] = title,
                        ["chunk_index"] = i,
                        ["source"] = "companyagent-dotnet",
                    };
                    if (!docs.ContainsKey(id)) order.Add(id);
                    docs[id] = IndexedDoc.From(new KnowledgeDocument(id, title, text, i, metadata));
                    added++;
                }
            }
            if (added > 0)
            {
                _snapshot = Snapshot.Build(order.Select(id => docs[id]).ToList());
                Interlocked.Increment(ref _version);
                Persist();
            }
            return added;
        }
    }

    public List<SearchResult> Search(string query, int topK)
    {
        if (string.IsNullOrWhiteSpace(query)) return [];
        var snapshot = _snapshot;
        var queryTokens = TextVectors.Tokenize(query);
        var bm25 = Normalize(Bm25Scores(snapshot, queryTokens));
        var queryVector = TextVectors.Embed(queryTokens);
        var vector = Normalize(snapshot.Docs.Select(d => Math.Max(0.0, TextVectors.Cosine(queryVector, d.Vector))).ToArray());

        return snapshot.Docs
            .Select((d, i) => (Doc: d.Doc, Score: _rag.Bm25Weight * bm25[i] + _rag.VectorWeight * vector[i]))
            .OrderByDescending(x => x.Score)
            .Take(topK)
            .Where(x => x.Score > 0.0)
            .Select(x => new SearchResult(x.Doc.Id, x.Doc.Title, x.Doc.Content, JsonText.Round(x.Score, 4), x.Doc.ChunkIndex, x.Doc.Metadata))
            .ToList();
    }

    private static double[] Bm25Scores(Snapshot snapshot, List<string> queryTerms)
    {
        var scores = new double[snapshot.Docs.Count];
        if (queryTerms.Count == 0 || snapshot.Docs.Count == 0) return scores;
        var n = snapshot.Docs.Count;
        for (var i = 0; i < n; i++)
        {
            var doc = snapshot.Docs[i];
            double score = 0;
            foreach (var term in queryTerms)
            {
                if (!doc.TermFrequencies.TryGetValue(term, out var tf)) continue;
                var df = snapshot.DocumentFrequency.GetValueOrDefault(term);
                var idf = Math.Log(1 + (n - df + 0.5) / (df + 0.5));
                var denominator = tf + K1 * (1 - B + B * doc.Length / snapshot.AverageLength);
                score += idf * (tf * (K1 + 1)) / denominator;
            }
            scores[i] = score;
        }
        return scores;
    }

    private static double[] Normalize(double[] scores)
    {
        var max = scores.Length == 0 ? 0.0 : scores.Max();
        return max <= 0.0 ? scores : scores.Select(s => s / max).ToArray();
    }

    private void LoadPersisted()
    {
        if (!File.Exists(_storePath)) return;
        try
        {
            var stored = JsonSerializer.Deserialize<List<StoredDocument>>(File.ReadAllText(_storePath), JsonText.Options) ?? [];
            var docs = new Dictionary<string, IndexedDoc>();
            foreach (var item in stored.Where(s => !string.IsNullOrWhiteSpace(s.Content)))
            {
                var title = string.IsNullOrWhiteSpace(item.Title) ? "未命名文档" : item.Title;
                var id = string.IsNullOrWhiteSpace(item.Id) ? Md5(title + item.ChunkIndex + item.Content) : item.Id;
                var metadata = item.Metadata?.ToDictionary(kv => kv.Key, kv => (object)kv.Value) ?? [];
                docs[id] = IndexedDoc.From(new KnowledgeDocument(id, title, item.Content!, item.ChunkIndex, metadata));
            }
            _snapshot = Snapshot.Build(docs.Values.ToList());
            _logger.LogInformation("Loaded {Count} persisted knowledge chunks from {Path}", docs.Count, _storePath);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Failed to load knowledge store {Path}: {Message}", _storePath, ex.Message);
        }
    }

    private void Persist()
    {
        try
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(_storePath));
            if (dir is not null) Directory.CreateDirectory(dir);
            var stored = _snapshot.Docs
                .Select(d => new StoredDocument(d.Doc.Id, d.Doc.Title, d.Doc.Content, d.Doc.ChunkIndex,
                    d.Doc.Metadata.ToDictionary(kv => kv.Key, kv => JsonSerializer.SerializeToElement(kv.Value))))
                .ToList();
            // Write to a temp file and rename, so a crash mid-write cannot corrupt the store.
            var tmp = _storePath + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(stored, JsonText.Indented));
            File.Move(tmp, _storePath, overwrite: true);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Failed to persist knowledge store {Path}: {Message}", _storePath, ex.Message);
        }
    }

    private static string Md5(string value) => Convert.ToHexString(MD5.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

    private static IEnumerable<(string, string)> DefaultDocuments() =>
    [
        ("退款政策", "用户在购买后 7 天内可以申请无理由退款。退款申请提交后，系统会在 1-3 个工作日内审核。审核通过后，款项将在 5-7 个工作日内退回原支付账户。商品已发货时，需要先完成退货流程。"),
        ("订单查询", "用户可以通过订单号查询订单状态。订单状态包括待支付、已支付、已发货、运输中、已签收、已完成。物流信息通常在发货后 24 小时内更新。"),
        ("账户安全", "建议用户定期修改密码，密码长度至少 8 位，包含字母和数字。如果忘记密码，可以通过绑定手机号或邮箱重置。发现异常登录时系统会锁定账户。"),
        ("技术故障排查", "应用崩溃请尝试清除缓存后重启应用。登录失败 401 表示认证失败。500 服务器错误通常是服务端问题，请稍后重试或联系技术支持。"),
        ("会员与积分", "每消费 1 元累积 1 积分。100 积分可抵扣 1 元。会员等级包括普通会员、银卡会员和金卡会员，积分有效期为 1 年。"),
        ("配送说明", "标准配送 3-5 个工作日送达，订单满 99 元免运费。加急配送 1-2 个工作日送达。同城配送可当日达或次日达。"),
    ];

    private sealed record StoredDocument(string? Id, string? Title, string? Content, int ChunkIndex,
        Dictionary<string, JsonElement>? Metadata);

    /// <summary>A document with its token statistics and vector precomputed at write time.</summary>
    private sealed record IndexedDoc(KnowledgeDocument Doc, Dictionary<string, int> TermFrequencies, int Length, double[] Vector)
    {
        public static IndexedDoc From(KnowledgeDocument doc)
        {
            var tokens = TextVectors.Tokenize(doc.Content);
            var tf = tokens.GroupBy(t => t).ToDictionary(g => g.Key, g => g.Count());
            return new IndexedDoc(doc, tf, tokens.Count, TextVectors.Embed(tokens));
        }
    }

    private sealed record Snapshot(IReadOnlyList<IndexedDoc> Docs, Dictionary<string, int> DocumentFrequency, double AverageLength)
    {
        public static readonly Snapshot Empty = new([], [], 1.0);

        public static Snapshot Build(IReadOnlyList<IndexedDoc> docs)
        {
            var df = new Dictionary<string, int>();
            foreach (var term in docs.SelectMany(d => d.TermFrequencies.Keys))
            {
                df[term] = df.GetValueOrDefault(term) + 1;
            }
            var avg = docs.Count == 0 ? 1.0 : Math.Max(1.0, docs.Average(d => d.Length));
            return new Snapshot(docs, df, avg);
        }
    }
}
