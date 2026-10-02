using System.Collections.Concurrent;
using StackExchange.Redis;

namespace CompanyAgent.Api.Memory;

/// <summary>Short-lived per-conversation state: a newest-first message list and a running summary.</summary>
public interface IWorkingMemoryStore
{
    Task PushAsync(string key, string value, TimeSpan ttl);
    Task<long> LengthAsync(string key);
    /// <summary>Newest-first, like Redis LRANGE after LPUSH.</summary>
    Task<IReadOnlyList<string>> RangeAsync(string key, int count);
    Task ReplaceAsync(string key, IReadOnlyList<string> newestFirst, TimeSpan ttl);
    Task<string?> GetAsync(string key);
    Task SetAsync(string key, string value, TimeSpan ttl);
}

public sealed class RedisWorkingMemoryStore(IConnectionMultiplexer redis) : IWorkingMemoryStore
{
    private IDatabase Db => redis.GetDatabase();

    public async Task PushAsync(string key, string value, TimeSpan ttl)
    {
        var tx = Db.CreateTransaction();
        _ = tx.ListLeftPushAsync(key, value);
        _ = tx.KeyExpireAsync(key, ttl);
        await tx.ExecuteAsync();
    }

    public Task<long> LengthAsync(string key) => Db.ListLengthAsync(key);

    public async Task<IReadOnlyList<string>> RangeAsync(string key, int count) =>
        (await Db.ListRangeAsync(key, 0, count - 1)).Select(v => v.ToString()).ToList();

    public async Task ReplaceAsync(string key, IReadOnlyList<string> newestFirst, TimeSpan ttl)
    {
        // Delete + refill in one MULTI/EXEC so readers never see an empty list mid-compression.
        var tx = Db.CreateTransaction();
        _ = tx.KeyDeleteAsync(key);
        if (newestFirst.Count > 0)
        {
            _ = tx.ListRightPushAsync(key, newestFirst.Select(v => (RedisValue)v).ToArray());
            _ = tx.KeyExpireAsync(key, ttl);
        }
        await tx.ExecuteAsync();
    }

    public async Task<string?> GetAsync(string key) => await Db.StringGetAsync(key);

    public Task SetAsync(string key, string value, TimeSpan ttl) => Db.StringSetAsync(key, value, ttl);
}

/// <summary>Process-local store for tests and local runs without Redis. TTLs are ignored.</summary>
public sealed class InMemoryWorkingMemoryStore : IWorkingMemoryStore
{
    private readonly ConcurrentDictionary<string, List<string>> _lists = new();
    private readonly ConcurrentDictionary<string, string> _values = new();

    public Task PushAsync(string key, string value, TimeSpan ttl)
    {
        var list = _lists.GetOrAdd(key, _ => []);
        lock (list) list.Insert(0, value);
        return Task.CompletedTask;
    }

    public Task<long> LengthAsync(string key) =>
        Task.FromResult(_lists.TryGetValue(key, out var list) ? (long)list.Count : 0L);

    public Task<IReadOnlyList<string>> RangeAsync(string key, int count)
    {
        if (!_lists.TryGetValue(key, out var list)) return Task.FromResult<IReadOnlyList<string>>([]);
        lock (list) return Task.FromResult<IReadOnlyList<string>>(list.Take(count).ToList());
    }

    public Task ReplaceAsync(string key, IReadOnlyList<string> newestFirst, TimeSpan ttl)
    {
        _lists[key] = newestFirst.ToList();
        return Task.CompletedTask;
    }

    public Task<string?> GetAsync(string key) => Task.FromResult(_values.GetValueOrDefault(key));

    public Task SetAsync(string key, string value, TimeSpan ttl)
    {
        _values[key] = value;
        return Task.CompletedTask;
    }
}
