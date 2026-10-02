using System.Text.RegularExpressions;

namespace CompanyAgent.Api.Knowledge;

/// <summary>Tokenisation and the local 256-dimension hashed n-gram vectors used for lexical-semantic search.</summary>
public static partial class TextVectors
{
    public const int Dimensions = 256;

    /// <summary>Whitespace words plus character 2- and 3-grams, which also covers Chinese text without a segmenter.</summary>
    public static List<string> Tokenize(string? text)
    {
        var normalized = PunctuationOrSpace().Replace((text ?? "").ToLowerInvariant(), " ");
        var tokens = normalized.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList();
        for (var n = 2; n <= 3; n++)
        {
            for (var i = 0; i + n <= normalized.Length; i++)
            {
                var gram = normalized.Substring(i, n).Trim();
                if (gram.Length > 0) tokens.Add(gram);
            }
        }
        return tokens;
    }

    /// <summary>Signed feature hashing of the distinct tokens.</summary>
    public static double[] Embed(IEnumerable<string> tokens)
    {
        var vector = new double[Dimensions];
        foreach (var token in tokens.Distinct())
        {
            var hash = StableHash(token);
            vector[(int)(hash % Dimensions)] += (hash & 1) == 0 ? 1.0 : -1.0;
        }
        return vector;
    }

    public static double Cosine(double[] a, double[] b)
    {
        double dot = 0, na = 0, nb = 0;
        for (var i = 0; i < a.Length; i++)
        {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }
        return na == 0 || nb == 0 ? 0.0 : dot / (Math.Sqrt(na) * Math.Sqrt(nb));
    }

    /// <summary>
    /// FNV-1a over UTF-16 code units. string.GetHashCode() is randomised per process in .NET,
    /// so it cannot be used for vectors that must stay comparable across restarts.
    /// </summary>
    public static uint StableHash(string value)
    {
        var hash = 2166136261u;
        foreach (var c in value)
        {
            hash ^= c;
            hash *= 16777619u;
        }
        return hash;
    }

    // ASCII punctuation (Java's \p{Punct}) and whitespace.
    [GeneratedRegex(@"[!-/:-@\[-`{-~\s]+")]
    private static partial Regex PunctuationOrSpace();
}
