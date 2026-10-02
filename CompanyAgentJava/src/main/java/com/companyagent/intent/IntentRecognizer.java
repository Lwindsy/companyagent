package com.companyagent.intent;

import com.companyagent.llm.LlmGateway;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class IntentRecognizer {

    private static final double CONFIDENCE_THRESHOLD = 0.5;

    private static final Map<IntentCategory, List<String>> TEMPLATES = Map.ofEntries(
            Map.entry(IntentCategory.QUERY, List.of("我的订单状态是什么？", "如何重置密码？", "快递什么时候到？")),
            Map.entry(IntentCategory.COMPLAINT, List.of("等了好几个小时！", "服务太差了！", "一直没人处理！")),
            Map.entry(IntentCategory.REQUEST, List.of("帮我取消订单", "我需要修改地址", "请协助退款")),
            Map.entry(IntentCategory.GREETING, List.of("你好", "嗨，有人吗", "早上好")),
            Map.entry(IntentCategory.ESCALATION, List.of("我要投诉！", "转人工客服", "找你们经理")),
            Map.entry(IntentCategory.TECHNICAL, List.of("应用一直崩溃", "无法登录", "出现500错误")),
            Map.entry(IntentCategory.BILLING, List.of("为什么扣了两次款？", "申请退款", "发票问题")),
            Map.entry(IntentCategory.ACCOUNT, List.of("修改邮箱", "注销账户", "更新个人信息")),
            Map.entry(IntentCategory.FEEDBACK, List.of("服务很棒！", "非常满意", "给个好评")),
            Map.entry(IntentCategory.ORDER_STATUS, List.of("我的订单现在是什么状态？", "订单有没有发货？", "订单处理到哪一步了？")),
            Map.entry(IntentCategory.LOGISTICS, List.of("快递什么时候到？", "物流一直不更新", "配送要多久？")),
            Map.entry(IntentCategory.REFUND, List.of("我要申请退款", "退货退款怎么处理？", "退款多久到账？")),
            Map.entry(IntentCategory.INVOICE, List.of("帮我开发票", "发票抬头怎么改？", "电子发票在哪里？")),
            Map.entry(IntentCategory.PAYMENT_ISSUE, List.of("为什么重复扣款？", "支付失败怎么办？", "这个月多扣了钱")),
            Map.entry(IntentCategory.ACCOUNT_SECURITY, List.of("账户被盗了", "发现异常登录", "我要重置密码")),
            Map.entry(IntentCategory.TECHNICAL_LOGIN, List.of("登录一直报401", "验证码收不到", "无法登录账号")),
            Map.entry(IntentCategory.TECHNICAL_CRASH, List.of("应用一直崩溃", "页面报500错误", "系统闪退")),
            Map.entry(IntentCategory.HUMAN_HANDOFF, List.of("转人工客服", "我要找人工", "请升级处理"))
    );

    private static final Set<IntentCategory> SPECIFIC_INTENTS = Set.of(
            IntentCategory.ORDER_STATUS,
            IntentCategory.LOGISTICS,
            IntentCategory.REFUND,
            IntentCategory.INVOICE,
            IntentCategory.PAYMENT_ISSUE,
            IntentCategory.ACCOUNT_SECURITY,
            IntentCategory.TECHNICAL_LOGIN,
            IntentCategory.TECHNICAL_CRASH,
            IntentCategory.HUMAN_HANDOFF
    );

    private static final Set<IntentCategory> GENERIC_INTENTS = Set.of(
            IntentCategory.QUERY,
            IntentCategory.BILLING,
            IntentCategory.TECHNICAL,
            IntentCategory.ACCOUNT,
            IntentCategory.ESCALATION
    );

    private static final Map<IntentCategory, IntentCategory> INTENT_GROUPS = Map.ofEntries(
            Map.entry(IntentCategory.ORDER_STATUS, IntentCategory.QUERY),
            Map.entry(IntentCategory.LOGISTICS, IntentCategory.QUERY),
            Map.entry(IntentCategory.REFUND, IntentCategory.BILLING),
            Map.entry(IntentCategory.INVOICE, IntentCategory.BILLING),
            Map.entry(IntentCategory.PAYMENT_ISSUE, IntentCategory.BILLING),
            Map.entry(IntentCategory.ACCOUNT_SECURITY, IntentCategory.ACCOUNT),
            Map.entry(IntentCategory.TECHNICAL_LOGIN, IntentCategory.TECHNICAL),
            Map.entry(IntentCategory.TECHNICAL_CRASH, IntentCategory.TECHNICAL),
            Map.entry(IntentCategory.HUMAN_HANDOFF, IntentCategory.ESCALATION)
    );

    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;
    private final Map<String, IntentResult> cache = new ConcurrentHashMap<>();

    public IntentRecognizer(LlmGateway llmGateway, ObjectMapper objectMapper) {
        this.llmGateway = llmGateway;
        this.objectMapper = objectMapper;
    }

    public IntentResult recognize(String message, List<Map<String, String>> history) {
        String key = cacheKey(message, history);
        IntentResult cached = cache.get(key);
        if (cached != null) {
            return cached;
        }
        Instant start = Instant.now();
        Map<String, Object> llm = llmRecognize(message, history);
        Map<String, Object> semantic = semanticRecognize(message);
        Map<String, Object> pattern = patternRecognize(message);
        VoteResult vote = vote(llm, semantic, pattern);
        IntentCategory intent = vote.intent();
        UrgencyLevel urgency = urgency(message, intent);
        IntentResult result = new IntentResult(
                intent,
                vote.confidence(),
                urgency,
                intentGroup(intent),
                extractEntities(message),
                String.valueOf(llm.getOrDefault("reasoning", "")),
                Duration.between(start, Instant.now()).toMillis(),
                vote.sourceScores()
        );
        if (cache.size() > 1000) {
            cache.clear();
        }
        cache.put(key, result);
        return result;
    }

    private Map<String, Object> llmRecognize(String message, List<Map<String, String>> history) {
        StringBuilder examples = new StringBuilder();
        TEMPLATES.forEach((intent, samples) ->
                examples.append("消息: \"").append(samples.getFirst()).append("\" -> 意图: ")
                        .append(intent.name().toLowerCase(Locale.ROOT)).append('\n'));
        String prompt = """
                你是客服意图分析专家。根据示例判断用户意图，返回 JSON。
                如果用户问题能匹配细粒度业务意图，请优先返回细粒度意图，而不是宽泛大类。
                例如退款优先返回 refund，发票优先返回 invoice，登录故障优先返回 technical_login。

                示例:
                %s
                最近对话:
                %s
                用户消息: "%s"
                返回格式: {"intent":"technical","confidence":0.9,"reasoning":"一句话说明"}
                可选意图: %s
                """.formatted(examples, history == null ? "" : history, message, allIntentNames());
        try {
            String raw = llmGateway.chat("", prompt, 0.1, 256);
            String json = sliceJsonObject(raw);
            Map<String, Object> data = objectMapper.readValue(json, new TypeReference<>() {
            });
            data.put("intent", parseIntent(String.valueOf(data.get("intent"))));
            return data;
        } catch (Exception ex) {
            Map<String, Object> fallback = new HashMap<>();
            fallback.put("intent", IntentCategory.OTHER);
            fallback.put("confidence", 0.0);
            fallback.put("reasoning", "LLM recognition failed");
            fallback.put("failed", true);
            return fallback;
        }
    }

    private Map<String, Object> semanticRecognize(String message) {
        IntentCategory best = IntentCategory.OTHER;
        double bestScore = 0.0;
        for (Map.Entry<IntentCategory, List<String>> entry : TEMPLATES.entrySet()) {
            for (String sample : entry.getValue()) {
                double score = jaccard(charNgrams(message), charNgrams(sample));
                if (score > bestScore) {
                    bestScore = score;
                    best = entry.getKey();
                }
            }
        }
        return Map.of("intent", best, "confidence", bestScore);
    }

    private Map<String, Object> patternRecognize(String message) {
        String msg = normalize(message);
        Map<IntentCategory, List<String>> specificPatterns = Map.ofEntries(
                Map.entry(IntentCategory.HUMAN_HANDOFF, List.of("转人工", "人工客服", "找人工")),
                Map.entry(IntentCategory.ORDER_STATUS, List.of("订单状态", "发货了吗", "处理到哪", "order status")),
                Map.entry(IntentCategory.LOGISTICS, List.of("物流", "快递", "配送", "运单", "delivery", "shipping")),
                Map.entry(IntentCategory.REFUND, List.of("退款", "退货", "refund", "return")),
                Map.entry(IntentCategory.INVOICE, List.of("发票", "抬头", "税号", "invoice")),
                Map.entry(IntentCategory.PAYMENT_ISSUE, List.of("重复扣款", "多扣", "支付失败", "扣费", "payment failed")),
                Map.entry(IntentCategory.ACCOUNT_SECURITY, List.of("被盗", "异常登录", "重置密码", "两步验证", "安全")),
                Map.entry(IntentCategory.TECHNICAL_LOGIN, List.of("无法登录", "登录失败", "401", "验证码")),
                Map.entry(IntentCategory.TECHNICAL_CRASH, List.of("崩溃", "闪退", "500", "报错", "crash"))
        );
        Map<String, Object> specific = bestPatternMatch(msg, specificPatterns);
        if (specific.get("intent") != IntentCategory.OTHER) {
            return specific;
        }

        Map<IntentCategory, List<String>> genericPatterns = Map.of(
                IntentCategory.ESCALATION, List.of("投诉", "经理", "supervisor"),
                IntentCategory.COMPLAINT, List.of("太差", "糟糕", "horrible", "等了很久"),
                IntentCategory.QUERY, List.of("?", "？", "怎么", "什么", "status"),
                IntentCategory.REQUEST, List.of("帮我", "需要", "please", "help"),
                IntentCategory.GREETING, List.of("你好", "嗨", "hello", "hi"),
                IntentCategory.BILLING, List.of("退款", "扣款", "发票", "refund"),
                IntentCategory.TECHNICAL, List.of("崩溃", "报错", "error", "crash"),
                IntentCategory.ACCOUNT, List.of("密码", "邮箱", "账户", "password")
        );
        return bestPatternMatch(msg, genericPatterns);
    }

    private Map<String, Object> bestPatternMatch(String msg, Map<IntentCategory, List<String>> patterns) {
        IntentCategory best = IntentCategory.OTHER;
        double bestScore = 0.0;
        for (Map.Entry<IntentCategory, List<String>> entry : patterns.entrySet()) {
            long hits = entry.getValue().stream().filter(msg::contains).count();
            if (hits > 0) {
                double score = Math.min(1.0, 0.5 + 0.25 * (hits - 1));
                if (score > bestScore) {
                    bestScore = score;
                    best = entry.getKey();
                }
            }
        }
        return Map.of("intent", best, "confidence", bestScore);
    }

    private VoteResult vote(Map<String, Object> llm, Map<String, Object> semantic, Map<String, Object> pattern) {
        Map<String, Double> sourceScores = new LinkedHashMap<>();
        sourceScores.put("llm", confidence(llm));
        sourceScores.put("embedding", confidence(semantic));
        sourceScores.put("pattern", confidence(pattern));

        if (Boolean.TRUE.equals(llm.get("failed"))) {
            if (semantic.get("intent") != IntentCategory.OTHER && confidence(semantic) > 0) {
                return new VoteResult((IntentCategory) semantic.get("intent"), confidence(semantic), sourceScores);
            }
            if (pattern.get("intent") != IntentCategory.OTHER && confidence(pattern) > 0) {
                return new VoteResult((IntentCategory) pattern.get("intent"), confidence(pattern), sourceScores);
            }
            return new VoteResult(IntentCategory.OTHER, 0.0, sourceScores);
        }
        Map<IntentCategory, Double> scores = new EnumMap<>(IntentCategory.class);
        addScore(scores, llm, 0.70);
        addScore(scores, semantic, 0.20);
        addScore(scores, pattern, 0.10);
        Map.Entry<IntentCategory, Double> best = scores.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .orElse(Map.entry(IntentCategory.OTHER, 0.0));
        IntentCategory patternIntent = (IntentCategory) pattern.getOrDefault("intent", IntentCategory.OTHER);
        double patternConfidence = confidence(pattern);
        if (GENERIC_INTENTS.contains(best.getKey())
                && SPECIFIC_INTENTS.contains(patternIntent)
                && patternConfidence >= 0.5
                && best.getValue() < 0.8) {
            sourceScores.put("refined_by_pattern", patternConfidence);
            return new VoteResult(patternIntent, Math.max(best.getValue(), patternConfidence), sourceScores);
        }
        if (best.getValue() < CONFIDENCE_THRESHOLD) {
            return new VoteResult(IntentCategory.OTHER, best.getValue(), sourceScores);
        }
        return new VoteResult(best.getKey(), best.getValue(), sourceScores);
    }

    private void addScore(Map<IntentCategory, Double> scores, Map<String, Object> result, double weight) {
        IntentCategory intent = (IntentCategory) result.getOrDefault("intent", IntentCategory.OTHER);
        double confidence = confidence(result);
        scores.merge(intent, weight * confidence, Double::sum);
    }

    private double confidence(Map<String, Object> result) {
        return ((Number) result.getOrDefault("confidence", 0.0)).doubleValue();
    }

    private Map<String, List<String>> extractEntities(String message) {
        Map<String, List<String>> entities = new LinkedHashMap<>();
        entities.put("order_id", regexFindGroup(message, "(?:订单号?|order(?:_id)?|#)\\s*[:：#]?\\s*([A-Za-z0-9_-]{4,32})", 1));
        entities.put("product", List.of());
        entities.put("date", regexFind(message, "(今天|明天|昨天|本周|这周|下周|\\d{4}[-/.年]\\d{1,2}[-/.月]\\d{1,2}日?)"));
        entities.put("error_code", regexFind(message, "\\b[45]\\d{2}\\b"));
        entities.put("amount", regexFind(message, "((?:¥|￥)\\s*\\d+(?:\\.\\d{1,2})?|\\d+(?:\\.\\d{1,2})?\\s*(?:元|块|rmb|cny|usd|美元))"));
        entities.put("error_code", uniqueConcat(
                entities.get("error_code"),
                regexFind(message, "\\b[A-Z][A-Z0-9_-]{2,16}\\b")
        ));
        return entities;
    }

    private List<String> regexFind(String text, String regex) {
        Matcher matcher = Pattern.compile(regex, Pattern.CASE_INSENSITIVE).matcher(text == null ? "" : text);
        List<String> values = new ArrayList<>();
        while (matcher.find()) {
            values.add(matcher.group());
        }
        return unique(values);
    }

    private List<String> regexFindGroup(String text, String regex, int group) {
        Matcher matcher = Pattern.compile(regex, Pattern.CASE_INSENSITIVE).matcher(text == null ? "" : text);
        List<String> values = new ArrayList<>();
        while (matcher.find()) {
            values.add(matcher.group(group));
        }
        return unique(values);
    }

    private List<String> uniqueConcat(List<String> left, List<String> right) {
        List<String> values = new ArrayList<>();
        if (left != null) {
            values.addAll(left);
        }
        if (right != null) {
            values.addAll(right);
        }
        return unique(values);
    }

    private List<String> unique(List<String> values) {
        return values.stream()
                .filter(value -> value != null && !value.isBlank())
                .map(String::trim)
                .distinct()
                .toList();
    }

    private UrgencyLevel urgency(String message, IntentCategory intent) {
        String msg = normalize(message);
        if (msg.contains("紧急") || msg.contains("urgent") || msg.contains("立刻")) {
            return UrgencyLevel.CRITICAL;
        }
        if (msg.contains("今天") || msg.contains("马上") || msg.contains("尽快")
                || intent == IntentCategory.ESCALATION || intent == IntentCategory.HUMAN_HANDOFF) {
            return UrgencyLevel.HIGH;
        }
        if (intent == IntentCategory.COMPLAINT) {
            return UrgencyLevel.MEDIUM;
        }
        return UrgencyLevel.LOW;
    }

    private IntentCategory parseIntent(String value) {
        try {
            return IntentCategory.valueOf(value.toUpperCase(Locale.ROOT));
        } catch (Exception ex) {
            return IntentCategory.OTHER;
        }
    }

    private String sliceJsonObject(String raw) {
        int start = raw.indexOf('{');
        int end = raw.lastIndexOf('}');
        if (start >= 0 && end > start) {
            return raw.substring(start, end + 1);
        }
        return "{}";
    }

    private List<String> charNgrams(String text) {
        String normalized = normalize(text);
        List<String> grams = new ArrayList<>();
        for (int n = 1; n <= 3; n++) {
            for (int i = 0; i + n <= normalized.length(); i++) {
                grams.add(normalized.substring(i, i + n));
            }
        }
        return grams;
    }

    private double jaccard(List<String> left, List<String> right) {
        if (left.isEmpty() || right.isEmpty()) {
            return 0.0;
        }
        java.util.Set<String> a = new java.util.HashSet<>(left);
        java.util.Set<String> b = new java.util.HashSet<>(right);
        long intersection = a.stream().filter(b::contains).count();
        long union = a.size() + b.size() - intersection;
        return union == 0 ? 0.0 : (double) intersection / union;
    }

    private String normalize(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT).trim();
    }

    private String cacheKey(String message, List<Map<String, String>> history) {
        StringBuilder key = new StringBuilder(normalize(message));
        if (history != null && !history.isEmpty()) {
            int start = Math.max(0, history.size() - 3);
            for (Map<String, String> item : history.subList(start, history.size())) {
                key.append('|')
                        .append(normalize(item.getOrDefault("role", "")))
                        .append(':')
                        .append(normalize(item.getOrDefault("content", "")));
            }
        }
        return key.toString();
    }

    private String intentGroup(IntentCategory intent) {
        return INTENT_GROUPS.getOrDefault(intent, intent).name().toLowerCase(Locale.ROOT);
    }

    private String allIntentNames() {
        List<String> names = new ArrayList<>();
        for (IntentCategory category : IntentCategory.values()) {
            names.add(category.name().toLowerCase(Locale.ROOT));
        }
        return String.join(", ", names);
    }

    private record VoteResult(IntentCategory intent, double confidence, Map<String, Double> sourceScores) {
    }
}
