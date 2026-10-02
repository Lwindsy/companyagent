using System.Threading.Channels;

namespace CompanyAgent.Api.Memory;

/// <summary>
/// Hands user-profile extraction off the request path. /chat enqueues and returns immediately;
/// <see cref="ProfileUpdateWorker"/> drains the channel in the background.
/// </summary>
public sealed class ProfileUpdateQueue
{
    // Bounded so a slow model cannot grow memory without limit; the oldest pending update is dropped.
    private readonly Channel<(string UserId, string ConversationId)> _channel =
        Channel.CreateBounded<(string, string)>(new BoundedChannelOptions(256)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
        });

    public void Enqueue(string userId, string conversationId) => _channel.Writer.TryWrite((userId, conversationId));

    public IAsyncEnumerable<(string UserId, string ConversationId)> ReadAllAsync(CancellationToken cancellationToken) =>
        _channel.Reader.ReadAllAsync(cancellationToken);
}

public sealed class ProfileUpdateWorker(ProfileUpdateQueue queue, MemoryManager memory, ILogger<ProfileUpdateWorker> logger)
    : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await foreach (var (userId, conversationId) in queue.ReadAllAsync(stoppingToken))
        {
            try
            {
                await memory.UpdateProfileAsync(userId, conversationId, stoppingToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                logger.LogWarning("Background profile update failed for {UserId}: {Message}", userId, ex.Message);
            }
        }
    }
}
