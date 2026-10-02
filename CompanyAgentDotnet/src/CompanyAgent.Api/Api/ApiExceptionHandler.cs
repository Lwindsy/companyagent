using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;

namespace CompanyAgent.Api.Api;

/// <summary>Maps domain exceptions to RFC 7807 problem responses.</summary>
public sealed class ApiExceptionHandler(IProblemDetailsService problemDetails, ILogger<ApiExceptionHandler> logger) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        var (status, title) = exception switch
        {
            AdminUnauthorizedException => (StatusCodes.Status401Unauthorized, "Unauthorized"),
            ArgumentException or FormatException or System.Text.Json.JsonException => (StatusCodes.Status400BadRequest, "Bad Request"),
            _ => (StatusCodes.Status500InternalServerError, "Internal Server Error"),
        };
        if (status == StatusCodes.Status500InternalServerError)
        {
            logger.LogError(exception, "Unhandled error on {Path}", httpContext.Request.Path);
        }

        httpContext.Response.StatusCode = status;
        return await problemDetails.TryWriteAsync(new ProblemDetailsContext
        {
            HttpContext = httpContext,
            Exception = exception,
            ProblemDetails = new ProblemDetails
            {
                Status = status,
                Title = title,
                // Internal error details stay in the log.
                Detail = status == StatusCodes.Status500InternalServerError ? "An unexpected error occurred." : exception.Message,
            },
        });
    }
}
