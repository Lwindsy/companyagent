package com.companyagent.agent;

public record AgentResponse(
        AgentType agentType,
        String content,
        boolean success,
        double confidence,
        long latencyMs,
        boolean escalate
) {
}
