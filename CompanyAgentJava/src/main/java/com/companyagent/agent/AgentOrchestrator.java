package com.companyagent.agent;

import com.companyagent.intent.IntentCategory;
import com.companyagent.intent.IntentRecognizer;
import com.companyagent.intent.IntentResult;
import com.companyagent.intent.UrgencyLevel;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;

@Service
public class AgentOrchestrator {

    private final IntentRecognizer intentRecognizer;
    private final Map<AgentType, List<BaseAgent>> pool;
    private final Map<IntentCategory, AgentType> routing = new EnumMap<>(IntentCategory.class);

    public AgentOrchestrator(IntentRecognizer intentRecognizer, Map<AgentType, List<BaseAgent>> pool) {
        this.intentRecognizer = intentRecognizer;
        this.pool = pool;
        routing.put(IntentCategory.TECHNICAL, AgentType.TECHNICAL);
        routing.put(IntentCategory.TECHNICAL_LOGIN, AgentType.TECHNICAL);
        routing.put(IntentCategory.TECHNICAL_CRASH, AgentType.TECHNICAL);
        routing.put(IntentCategory.BILLING, AgentType.BILLING);
        routing.put(IntentCategory.REFUND, AgentType.BILLING);
        routing.put(IntentCategory.INVOICE, AgentType.BILLING);
        routing.put(IntentCategory.PAYMENT_ISSUE, AgentType.BILLING);
        routing.put(IntentCategory.ACCOUNT, AgentType.BILLING);
        routing.put(IntentCategory.ACCOUNT_SECURITY, AgentType.BILLING);
        routing.put(IntentCategory.ESCALATION, AgentType.ESCALATION);
        routing.put(IntentCategory.HUMAN_HANDOFF, AgentType.ESCALATION);
    }

    public OrchestratorResult run(AgentRequest request) {
        Instant start = Instant.now();
        AgentRequest req = request;
        if (req.intent() == null) {
            IntentResult intentResult = intentRecognizer.recognize(req.message(), req.history());
            req = req.withIntent(intentResult);
        }

        if (needsClarification(req)) {
            return new OrchestratorResult(
                    req.requestId(),
                    "我还不能确定您要处理的是哪类问题。请补充一下是订单物流、退款账单、账户资料，还是技术故障？",
                    AgentType.GENERAL,
                    req.intent(),
                    false,
                    Duration.between(start, Instant.now()).toMillis(),
                    List.of(AgentType.GENERAL),
                    AgentType.GENERAL,
                    List.of(),
                    "低置信度 OTHER 意图，先澄清用户需求",
                    req.intentConfidence()
            );
        }

        RoutingDecision decision = routeDecision(req);
        if (decision.multiAgent()) {
            return runParallel(req, decision);
        }

        AgentResponse response = execute(req, decision.primaryAgent());
        boolean escalated = response.escalate()
                || req.urgency() == UrgencyLevel.CRITICAL
                || req.intent() == IntentCategory.ESCALATION
                || req.intent() == IntentCategory.HUMAN_HANDOFF;
        return new OrchestratorResult(
                req.requestId(),
                response.content(),
                response.agentType(),
                req.intent(),
                escalated,
                Duration.between(start, Instant.now()).toMillis(),
                List.of(response.agentType()),
                decision.primaryAgent(),
                List.of(),
                decision.reason(),
                decision.confidence()
        );
    }

    private OrchestratorResult runParallel(AgentRequest req, RoutingDecision decision) {
        Instant start = Instant.now();
        List<AgentType> targets = decision.agentTypes();
        List<CompletableFuture<AgentResponse>> futures = targets.stream()
                .map(type -> CompletableFuture.supplyAsync(() -> execute(req, type)))
                .toList();
        List<AgentResponse> responses = futures.stream().map(CompletableFuture::join).toList();
        List<String> parts = new ArrayList<>();
        for (AgentResponse response : responses) {
            if (response.success()) {
                String role = response.agentType() == decision.primaryAgent() ? "主处理" : "辅助处理";
                parts.add("[" + response.agentType().name().toLowerCase(Locale.ROOT) + " - " + role + "]\n" + response.content());
            }
        }
        String content = parts.isEmpty() ? "抱歉，所有 Agent 均处理失败。" : String.join("\n\n", parts);
        boolean escalate = responses.stream().anyMatch(AgentResponse::escalate);
        List<AgentType> agentTypes = responses.stream()
                .filter(AgentResponse::success)
                .map(AgentResponse::agentType)
                .toList();
        return new OrchestratorResult(
                req.requestId(),
                content,
                decision.primaryAgent(),
                req.intent(),
                escalate,
                Duration.between(start, Instant.now()).toMillis(),
                agentTypes.isEmpty() ? targets : agentTypes,
                decision.primaryAgent(),
                decision.supportingAgents(),
                decision.reason(),
                decision.confidence()
        );
    }

    private AgentType route(IntentCategory intent, UrgencyLevel urgency) {
        if (urgency == UrgencyLevel.CRITICAL) {
            return AgentType.ESCALATION;
        }
        AgentType target = routing.get(intent);
        if (target != null && pool.containsKey(target)) {
            return target;
        }
        return AgentType.GENERAL;
    }

    private RoutingDecision routeDecision(AgentRequest req) {
        if (req.urgency() == UrgencyLevel.CRITICAL) {
            return new RoutingDecision(AgentType.ESCALATION, List.of(), "紧急度为 CRITICAL，触发升级路由", 1.0);
        }
        if (req.intent() == IntentCategory.ESCALATION || req.intent() == IntentCategory.HUMAN_HANDOFF) {
            String intent = req.intent() == null ? "unknown" : req.intent().name().toLowerCase(Locale.ROOT);
            return new RoutingDecision(
                    AgentType.ESCALATION,
                    List.of(),
                    "意图为 " + intent + "，触发升级路由",
                    Math.max(req.intentConfidence(), 0.8)
            );
        }

        Map<AgentType, Double> scores = domainScores(req);
        Map<AgentType, Double> availableScores = new EnumMap<>(AgentType.class);
        scores.forEach((agentType, score) -> {
            if (agentType == AgentType.GENERAL || pool.containsKey(agentType)) {
                availableScores.put(agentType, score);
            }
        });
        if (availableScores.isEmpty()) {
            return new RoutingDecision(AgentType.GENERAL, List.of(), "无可用专属 Agent，降级到 GeneralAgent", 0.1);
        }

        List<Map.Entry<AgentType, Double>> ordered = availableScores.entrySet().stream()
                .sorted(Map.Entry.<AgentType, Double>comparingByValue().reversed())
                .toList();
        AgentType primary = ordered.getFirst().getKey();
        double primaryScore = ordered.getFirst().getValue();
        List<AgentType> supportingAgents = ordered.stream()
                .skip(1)
                .filter(entry -> entry.getKey() != AgentType.GENERAL)
                .filter(entry -> entry.getValue() >= 0.45 && entry.getValue() >= primaryScore * 0.55)
                .map(Map.Entry::getKey)
                .toList();
        return new RoutingDecision(
                primary,
                supportingAgents,
                routingReason(req, availableScores, primary, supportingAgents),
                round(Math.min(primaryScore, 1.0))
        );
    }

    private Map<AgentType, Double> domainScores(AgentRequest req) {
        String msg = req.message() == null ? "" : req.message().toLowerCase(Locale.ROOT);
        Map<AgentType, Double> scores = new EnumMap<>(AgentType.class);
        scores.put(AgentType.GENERAL, 0.1);
        scores.put(AgentType.TECHNICAL, 0.0);
        scores.put(AgentType.BILLING, 0.0);

        if (List.of(
                IntentCategory.QUERY,
                IntentCategory.ORDER_STATUS,
                IntentCategory.LOGISTICS,
                IntentCategory.REQUEST,
                IntentCategory.COMPLAINT,
                IntentCategory.GREETING,
                IntentCategory.FEEDBACK,
                IntentCategory.OTHER
        ).contains(req.intent())) {
            scores.merge(AgentType.GENERAL, 0.55, Double::sum);
        }

        if (List.of(
                IntentCategory.TECHNICAL,
                IntentCategory.TECHNICAL_LOGIN,
                IntentCategory.TECHNICAL_CRASH
        ).contains(req.intent())) {
            scores.merge(AgentType.TECHNICAL, 0.75, Double::sum);
        }

        if (List.of(
                IntentCategory.BILLING,
                IntentCategory.ACCOUNT,
                IntentCategory.ACCOUNT_SECURITY,
                IntentCategory.REFUND,
                IntentCategory.INVOICE,
                IntentCategory.PAYMENT_ISSUE
        ).contains(req.intent())) {
            scores.merge(AgentType.BILLING, 0.75, Double::sum);
        }

        long technicalHits = countHits(msg, "崩溃", "报错", "error", "crash", "无法登录", "登录失败", "500", "401", "验证码");
        long billingHits = countHits(msg, "退款", "退货", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice", "多扣");
        long generalHits = countHits(msg, "订单", "物流", "快递", "配送", "会员", "积分", "咨询", "帮助");

        scores.merge(AgentType.TECHNICAL, Math.min(0.45, technicalHits * 0.18), Double::sum);
        scores.merge(AgentType.BILLING, Math.min(0.45, billingHits * 0.18), Double::sum);
        scores.merge(AgentType.GENERAL, Math.min(0.35, generalHits * 0.12), Double::sum);

        Map<String, List<String>> entities = req.entities() == null ? Map.of() : req.entities();
        if (!entities.getOrDefault("error_code", List.of()).isEmpty()) {
            scores.merge(AgentType.TECHNICAL, 0.2, Double::sum);
        }
        if (!entities.getOrDefault("amount", List.of()).isEmpty()) {
            scores.merge(AgentType.BILLING, 0.15, Double::sum);
        }
        if (!entities.getOrDefault("order_id", List.of()).isEmpty()) {
            scores.merge(AgentType.GENERAL, 0.1, Double::sum);
        }

        scores.replaceAll((agentType, score) -> round(score));
        return scores;
    }

    private String routingReason(
            AgentRequest req,
            Map<AgentType, Double> scores,
            AgentType primaryAgent,
            List<AgentType> supportingAgents
    ) {
        String scoreText = scores.entrySet().stream()
                .sorted(Map.Entry.<AgentType, Double>comparingByValue().reversed())
                .map(entry -> entry.getKey().name().toLowerCase(Locale.ROOT) + "=" + String.format(Locale.ROOT, "%.2f", entry.getValue()))
                .reduce((left, right) -> left + ", " + right)
                .orElse("");
        String supportText = supportingAgents.stream()
                .map(agent -> agent.name().toLowerCase(Locale.ROOT))
                .reduce((left, right) -> left + ", " + right)
                .orElse("none");
        String intent = req.intent() == null ? "unknown" : req.intent().name().toLowerCase(Locale.ROOT);
        return "intent=" + intent
                + ", group=" + (req.intentGroup() == null ? "unknown" : req.intentGroup())
                + ", primary=" + primaryAgent.name().toLowerCase(Locale.ROOT)
                + ", supporting=" + supportText
                + ", scores=[" + scoreText + "]";
    }

    private List<AgentType> collaborationTargets(AgentRequest request) {
        String msg = request.message() == null ? "" : request.message().toLowerCase(Locale.ROOT);
        LinkedHashSet<AgentType> targets = new LinkedHashSet<>();
        if (request.intent() == IntentCategory.TECHNICAL || containsAny(msg, "崩溃", "报错", "error", "crash", "无法登录", "500", "401")) {
            targets.add(AgentType.TECHNICAL);
        }
        if (request.intent() == IntentCategory.BILLING || request.intent() == IntentCategory.ACCOUNT
                || containsAny(msg, "退款", "扣款", "发票", "账单", "支付", "订阅", "refund", "invoice")) {
            targets.add(AgentType.BILLING);
        }
        return new ArrayList<>(targets.stream().filter(pool::containsKey).toList());
    }

    private boolean containsAny(String text, String... keywords) {
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private long countHits(String text, String... keywords) {
        long hits = 0;
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                hits++;
            }
        }
        return hits;
    }

    private boolean needsClarification(AgentRequest req) {
        if (req.intent() != IntentCategory.OTHER) {
            return false;
        }
        String text = req.message() == null ? "" : req.message().trim();
        if (text.length() <= 2) {
            return false;
        }
        return req.intentConfidence() < 0.5;
    }

    private AgentResponse execute(AgentRequest req, AgentType agentType) {
        BaseAgent agent = bestAgent(agentType).orElseGet(() -> bestAgent(AgentType.GENERAL).orElse(null));
        if (agent == null) {
            return new AgentResponse(AgentType.GENERAL, "服务暂时不可用，请稍后重试。", false, 0.0, 0, false);
        }
        AgentResponse response = agent.handle(req);
        if (!response.success() && agentType != AgentType.GENERAL) {
            return bestAgent(AgentType.GENERAL).map(a -> a.handle(req)).orElse(response);
        }
        return response;
    }

    private Optional<BaseAgent> bestAgent(AgentType agentType) {
        return pool.getOrDefault(agentType, List.of()).stream()
                .max(Comparator.comparingDouble(a -> a.stats().routingScore()));
    }

    public Map<String, Object> stats() {
        Map<String, Object> result = new HashMap<>();
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                BaseAgent agent = agents.get(i);
                Map<String, Object> data = new HashMap<>();
                data.put("total", agent.stats().total());
                data.put("success_rate", round(agent.stats().successRate()));
                data.put("avg_ms", round(agent.stats().avgLatencyMs()));
                data.put("monitor_penalty", round(agent.stats().monitorPenalty()));
                data.put("routing_score", round(agent.stats().routingScore()));
                result.put(type.name().toLowerCase(Locale.ROOT) + "_" + i, data);
            }
        });
        return result;
    }

    @SuppressWarnings("unchecked")
    public void updateRoutingPenalties(Map<String, Double> penalties) {
        pool.forEach((type, agents) -> {
            for (int i = 0; i < agents.size(); i++) {
                agents.get(i).stats().setMonitorPenalty(penalties.getOrDefault(type.name().toLowerCase(Locale.ROOT) + "_" + i, 0.0));
            }
        });
    }

    private double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }

    private record RoutingDecision(
            AgentType primaryAgent,
            List<AgentType> supportingAgents,
            String reason,
            double confidence
    ) {
        private List<AgentType> agentTypes() {
            List<AgentType> result = new ArrayList<>();
            result.add(primaryAgent);
            result.addAll(supportingAgents);
            return result;
        }

        private boolean multiAgent() {
            return !supportingAgents.isEmpty();
        }
    }
}
