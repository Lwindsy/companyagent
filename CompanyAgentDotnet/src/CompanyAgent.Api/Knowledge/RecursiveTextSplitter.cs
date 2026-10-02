namespace CompanyAgent.Api.Knowledge;

/// <summary>
/// Splits text into chunks of at most <c>maxChars</c>, preferring paragraph, then line, then sentence
/// boundaries, and carries up to <c>overlapChars</c> of the previous chunk into the next one.
/// </summary>
public sealed class RecursiveTextSplitter(int maxChars = 500, int overlapChars = 80)
{
    private static readonly string[][] Separators =
    [
        ["\r\n\r\n", "\n\n"],
        ["\r\n", "\n"],
        ["。", "！", "？", ". ", "! ", "? ", "；", "; "],
    ];

    public List<string> Split(string? text)
    {
        var pieces = SplitRecursive((text ?? "").Trim(), 0);
        return Merge(pieces);
    }

    private List<string> SplitRecursive(string text, int level)
    {
        if (text.Length <= maxChars) return text.Length == 0 ? [] : [text];
        if (level >= Separators.Length)
        {
            // No natural boundary left: hard cut.
            var hard = new List<string>();
            for (var start = 0; start < text.Length; start += maxChars)
            {
                hard.Add(text.Substring(start, Math.Min(maxChars, text.Length - start)));
            }
            return hard;
        }

        var parts = SplitKeepingSeparators(text, Separators[level]);
        if (parts.Count <= 1) return SplitRecursive(text, level + 1);
        return parts.SelectMany(part => SplitRecursive(part, level + 1)).ToList();
    }

    private static List<string> SplitKeepingSeparators(string text, string[] separators)
    {
        var parts = new List<string>();
        var start = 0;
        for (var i = 0; i < text.Length; i++)
        {
            foreach (var sep in separators)
            {
                if (string.CompareOrdinal(text, i, sep, 0, sep.Length) != 0) continue;
                var end = i + sep.Length;
                parts.Add(text[start..end]);
                start = end;
                i = end - 1;
                break;
            }
        }
        if (start < text.Length) parts.Add(text[start..]);
        return parts.Where(p => p.Trim().Length > 0).ToList();
    }

    private List<string> Merge(List<string> pieces)
    {
        var chunks = new List<string>();
        var current = "";
        foreach (var piece in pieces)
        {
            if (current.Length + piece.Length <= maxChars)
            {
                current += piece;
                continue;
            }
            if (current.Trim().Length > 0) chunks.Add(current.Trim());
            var overlap = current.Length > overlapChars ? current[^overlapChars..] : current;
            current = overlap.Length + piece.Length <= maxChars ? overlap + piece : piece;
        }
        if (current.Trim().Length > 0) chunks.Add(current.Trim());
        return chunks;
    }
}
