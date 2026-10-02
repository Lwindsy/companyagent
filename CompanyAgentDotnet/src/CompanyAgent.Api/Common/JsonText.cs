using System.Text.Json;
using System.Text.Json.Nodes;

namespace CompanyAgent.Api.Common;

/// <summary>Helpers for pulling JSON out of free-form LLM replies and for rounding scores.</summary>
public static class JsonText
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = null,
        WriteIndented = false,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static readonly JsonSerializerOptions Indented = new(Options) { WriteIndented = true };

    /// <summary>Returns the outermost {...} span of <paramref name="raw"/>, or null.</summary>
    public static JsonObject? ExtractObject(string? raw)
    {
        var slice = Slice(raw, '{', '}');
        return slice is null ? null : JsonNode.Parse(slice) as JsonObject;
    }

    /// <summary>Returns the outermost [...] span of <paramref name="raw"/>, or null.</summary>
    public static JsonArray? ExtractArray(string? raw)
    {
        var slice = Slice(raw, '[', ']');
        return slice is null ? null : JsonNode.Parse(slice) as JsonArray;
    }

    private static string? Slice(string? raw, char open, char close)
    {
        if (string.IsNullOrEmpty(raw)) return null;
        var start = raw.IndexOf(open);
        var end = raw.LastIndexOf(close);
        return start >= 0 && end > start ? raw[start..(end + 1)] : null;
    }

    public static double? AsDouble(JsonNode? node)
    {
        if (node is not JsonValue value) return null;
        if (value.TryGetValue<double>(out var d)) return d;
        if (value.TryGetValue<string>(out var s) &&
            double.TryParse(s, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var parsed))
            return parsed;
        return null;
    }

    public static bool IsTrue(JsonNode? node) => node is JsonValue v && v.TryGetValue<bool>(out var b) && b;

    public static double Round(double value, int digits) => Math.Round(value, digits, MidpointRounding.AwayFromZero);
}
