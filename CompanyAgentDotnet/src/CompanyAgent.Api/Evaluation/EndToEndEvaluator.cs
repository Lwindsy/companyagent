using System.Text.Json;
using System.Text.Json.Nodes;
using CompanyAgent.Api.Agents;
using CompanyAgent.Api.Common;
using CompanyAgent.Api.Configuration;
using CompanyAgent.Api.Intent;
using CompanyAgent.Api.Llm;
using Microsoft.Extensions.Options;

namespace CompanyAgent.Api.Evaluation;

public sealed record EvalRunRequest(List<IntentCase>? IntentCases, List<DialogCase>? DialogCases);

public sealed record IntentCase(string Message, string ExpectedIntent);

public sealed record DialogCase(string? Question, List<string>? Turns, string? UserId, string? ConversationId);

public sealed record QualityScores(double Relevance, double Accuracy, double Completeness, double Helpfulness, bool JudgeFailed, string? Error)
{
    public double Overall => (Relevance + Accuracy + Completeness + Helpfulness) / 4.0;
}

/// <summary>LLM-as-Judge scoring on relevance, accuracy, completeness and helpfulness (0–1 each).</summary>
public sealed class LlmJudge(ILlmGateway llm)
{
    public async Task<QualityScores> JudgeAsync(string question, string response, string? context, CancellationToken cancellationToken)
    {
        var prompt = $$"""
            你是客服质量评估专家。请对以下客服响应进行评分。
            用户问题: {{question}}
            Agent 响应: {{response}}
            背景信息: {{context ?? ""}}

            从 relevance、accuracy、completeness、helpfulness 四个维度评分，范围 0.0-1.0。
            只返回 JSON，例如 {"relevance":0.9,"accuracy":0.8,"completeness":0.7,"helpfulness":0.85}
            """;
        try
        {
            var raw = await llm.ChatAsync("", prompt, 0.0, 256, cancellationToken);
            var data = JsonText.ExtractObject(raw) ?? throw new FormatException("No JSON object in judge reply");
            double Score(string name) => Math.Clamp(JsonText.AsDouble(data[name]) ?? 0.5, 0.0, 1.0);
            return new QualityScores(Score("relevance"), Score("accuracy"), Score("completeness"), Score("helpfulness"), false, null);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            return new QualityScores(0.5, 0.5, 0.5, 0.5, true, ex.Message);
        }
    }
}

/// <summary>
/// Runs intent cases (accuracy, per-class P/R/F1, macro-F1) and dialog cases (LLM-as-Judge),
/// compares averages with the saved baseline to flag regressions over 5%, then saves a new baseline.
/// </summary>
public sealed class EndToEndEvaluator(IntentRecognizer intentRecognizer, AgentOrchestrator orchestrator, LlmJudge judge,
    IOptions<CompanyAgentOptions> options)
{
    private readonly string _baselinePath = options.Value.Eval.BaselinePath;

    public async Task<Dictionary<string, object>> RunAsync(EvalRunRequest? request, CancellationToken cancellationToken)
    {
        var intentCases = request?.IntentCases ?? DefaultIntentCases();
        var dialogCases = request?.DialogCases ?? DefaultDialogCases();
        var results = new List<Dictionary<string, object>>();
        var predictions = new List<string>();
        var truth = new List<string>();
        var dialogOverall = new List<double>();

        foreach (var c in intentCases)
        {
            var predicted = (await intentRecognizer.RecognizeAsync(c.Message, null, cancellationToken)).Intent.Name();
            predictions.Add(predicted);
            truth.Add(c.ExpectedIntent);
            var passed = predicted == c.ExpectedIntent;
            results.Add(new()
            {
                ["test_id"] = $"intent_{results.Count}",
                ["passed"] = passed,
                ["scores"] = new Dictionary<string, double> { ["accuracy"] = passed ? 1.0 : 0.0 },
                ["metadata"] = new Dictionary<string, string> { ["message"] = c.Message, ["expected"] = c.ExpectedIntent, ["predicted"] = predicted },
            });
        }

        foreach (var c in dialogCases)
        {
            var turns = c.Turns is { Count: > 0 } ? c.Turns : [c.Question ?? ""];
            var history = new List<ChatTurn>();
            var conversationId = c.ConversationId ?? $"eval_{Guid.NewGuid()}";
            var userId = c.UserId ?? "eval_user";
            foreach (var turn in turns)
            {
                var historyText = string.Join("\n", history);
                var response = await orchestrator.RunAsync(
                    AgentRequest.Create(turn, userId, conversationId, historyText, history.ToList()), cancellationToken);
                var scores = await judge.JudgeAsync(turn, response.Response, historyText, cancellationToken);
                dialogOverall.Add(scores.Overall);
                results.Add(new()
                {
                    ["test_id"] = $"dialog_{results.Count}",
                    ["passed"] = scores.Overall >= 0.75,
                    ["scores"] = new Dictionary<string, double>
                    {
                        ["overall"] = Round(scores.Overall),
                        ["relevance"] = scores.Relevance,
                        ["accuracy"] = scores.Accuracy,
                        ["completeness"] = scores.Completeness,
                        ["helpfulness"] = scores.Helpfulness,
                    },
                    ["metadata"] = new Dictionary<string, object>
                    {
                        ["question"] = turn,
                        ["response"] = response.Response,
                        ["agent_type"] = response.AgentType.Name(),
                        ["judge_failed"] = scores.JudgeFailed,
                        ["judge_error"] = scores.Error ?? "",
                    },
                });
                history.Add(new ChatTurn("user", turn));
                history.Add(new ChatTurn("assistant", response.Response));
            }
        }

        var passedCount = results.Count(r => (bool)r["passed"]);
        var intentAccuracy = intentCases.Count == 0 ? 0.0 : (double)predictions.Zip(truth).Count(p => p.First == p.Second) / intentCases.Count;
        var perClass = Metrics.PerClass(predictions, truth);
        var avgScores = new Dictionary<string, double>
        {
            ["intent_accuracy"] = Round(intentAccuracy),
            ["macro_f1"] = Round(perClass.Count == 0 ? 0.0 : perClass.Values.Average(m => m["f1"])),
            ["dialog_overall"] = Round(dialogOverall.Count == 0 ? 0.0 : dialogOverall.Average()),
        };
        var regressions = DetectRegressions(avgScores);
        var report = new Dictionary<string, object>
        {
            ["pass_rate"] = results.Count == 0 ? 0.0 : Round((double)passedCount / results.Count),
            ["total"] = results.Count,
            ["passed"] = passedCount,
            ["avg_scores"] = avgScores,
            ["per_class"] = perClass,
            ["regressions"] = regressions,
            ["recommendations"] = Recommendations(intentAccuracy, regressions),
            ["results"] = results,
        };
        SaveBaseline(report);
        return report;
    }

    private List<string> DetectRegressions(Dictionary<string, double> current)
    {
        if (!File.Exists(_baselinePath)) return [];
        try
        {
            var previous = JsonNode.Parse(File.ReadAllText(_baselinePath))?["avg_scores"] as JsonObject;
            var regressions = new List<string>();
            foreach (var (metric, value) in current)
            {
                if (JsonText.AsDouble(previous?[metric]) is not { } prev || prev <= 0) continue;
                if ((value - prev) / prev < -0.05)
                {
                    regressions.Add($"{metric}: {Round(prev)} -> {Round(value)}");
                }
            }
            return regressions;
        }
        catch (Exception)
        {
            return [];
        }
    }

    private void SaveBaseline(Dictionary<string, object> report)
    {
        try
        {
            var dir = Path.GetDirectoryName(Path.GetFullPath(_baselinePath));
            if (dir is not null) Directory.CreateDirectory(dir);
            File.WriteAllText(_baselinePath, JsonSerializer.Serialize(report, JsonText.Indented));
        }
        catch (Exception)
        {
            // A read-only volume must not fail the evaluation itself.
        }
    }

    private static List<string> Recommendations(double intentAccuracy, List<string> regressions)
    {
        var recs = new List<string>();
        if (intentAccuracy < 0.9) recs.Add("补充低准确率意图类别的样本和 Few-shot 示例");
        if (regressions.Count > 0) recs.Add("发现评测回归，请对比 baseline 中退化指标并检查最近 prompt 或检索逻辑变更");
        if (recs.Count == 0) recs.Add("所有指标均达标");
        return recs;
    }

    private static double Round(double value) => JsonText.Round(value, 4);

    private static List<IntentCase> DefaultIntentCases() =>
    [
        new("我的订单什么时候到？", "query"),
        new("帮我取消订单", "request"),
        new("你们服务太差了！", "complaint"),
        new("应用一直报500错误", "technical"),
        new("为什么扣了两次款？", "billing"),
        new("我要投诉，转人工！", "escalation"),
        new("你好", "greeting"),
        new("修改我的邮箱地址", "account"),
    ];

    private static List<DialogCase> DefaultDialogCases() =>
    [
        new("我的订单 #12345 还没到，已经超时了", null, null, null),
        new("应用登录一直报错 401", null, null, null),
        new("为什么这个月多扣了 50 块钱？", null, null, null),
        new(null, ["你好，我想退款", "订单号是 #12345", "退款多久能到账？"], null, null),
    ];
}

public static class Metrics
{
    /// <summary>Per-label precision, recall and F1 over paired predictions and ground truth.</summary>
    public static Dictionary<string, Dictionary<string, double>> PerClass(IReadOnlyList<string> predictions, IReadOnlyList<string> truth)
    {
        var metrics = new Dictionary<string, Dictionary<string, double>>();
        foreach (var label in predictions.Union(truth))
        {
            int tp = 0, fp = 0, fn = 0;
            for (var i = 0; i < predictions.Count; i++)
            {
                var p = predictions[i] == label;
                var g = truth[i] == label;
                if (p && g) tp++;
                if (p && !g) fp++;
                if (!p && g) fn++;
            }
            var precision = tp + fp == 0 ? 0.0 : (double)tp / (tp + fp);
            var recall = tp + fn == 0 ? 0.0 : (double)tp / (tp + fn);
            var f1 = precision + recall == 0 ? 0.0 : 2 * precision * recall / (precision + recall);
            metrics[label] = new Dictionary<string, double>
            {
                ["precision"] = JsonText.Round(precision, 4),
                ["recall"] = JsonText.Round(recall, 4),
                ["f1"] = JsonText.Round(f1, 4),
            };
        }
        return metrics;
    }
}
