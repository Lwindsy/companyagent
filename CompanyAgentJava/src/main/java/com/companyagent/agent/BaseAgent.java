package com.companyagent.agent;

import com.companyagent.llm.LlmGateway;
import com.companyagent.skill.SkillManager;

import java.time.Duration;
import java.time.Instant;

public abstract class BaseAgent {

    private final LlmGateway llmGateway;
    private final SkillManager skillManager;
    private final AgentStats stats = new AgentStats();

    protected BaseAgent(LlmGateway llmGateway, SkillManager skillManager) {
        this.llmGateway = llmGateway;
        this.skillManager = skillManager;
    }

    public abstract AgentType type();

    protected abstract String systemPrompt();

    public AgentResponse handle(AgentRequest request) {
        Instant start = Instant.now();
        try {
            String prompt = buildPrompt(request);
            String content = llmGateway.chat(buildSystemPrompt(request), prompt, 0.2, 1024);
            long latency = Duration.between(start, Instant.now()).toMillis();
            boolean escalate = needsEscalation(content);
            stats.record(true, latency);
            return new AgentResponse(type(), content, true, 1.0, latency, escalate);
        } catch (Exception ex) {
            long latency = Duration.between(start, Instant.now()).toMillis();
            stats.record(false, latency);
            return new AgentResponse(type(), "抱歉，处理您的请求时出现问题，请稍后重试。", false, 0.0, latency, false);
        }
    }

    public AgentStats stats() {
        return stats;
    }

    private String buildPrompt(AgentRequest request) {
        StringBuilder prompt = new StringBuilder();
        if (request.context() != null && !request.context().isBlank()) {
            prompt.append("[背景信息]\n").append(request.context()).append("\n\n");
        }
        if (request.entities() != null && !request.entities().isEmpty()) {
            prompt.append("[结构化实体]\n").append(request.entities()).append("\n\n");
        }
        prompt.append("[用户问题]\n").append(request.message());
        return prompt.toString();
    }

    private String buildSystemPrompt(AgentRequest request) {
        if (skillManager == null) {
            return systemPrompt();
        }
        String skillPrompt = skillManager.promptFor(request.message(), type().name().toLowerCase());
        if (skillPrompt.isBlank()) {
            return systemPrompt();
        }
        return systemPrompt() + "\n\n[动态 Skills]\n" + skillPrompt;
    }

    private boolean needsEscalation(String content) {
        String text = content == null ? "" : content.toLowerCase();
        return text.contains("转人工")
                || text.contains("人工客服")
                || text.contains("escalate")
                || text.contains("specialist")
                || text.contains("无法处理");
    }
}
