package com.companyagent.agent;

import com.companyagent.intent.IntentCategory;

import java.util.List;

public record OrchestratorResult(
        String requestId,
        String response,
        AgentType agentType,
        IntentCategory intent,
        boolean escalated,
        long latencyMs,
        List<AgentType> agentTypes,
        AgentType primaryAgent,
        List<AgentType> supportingAgents,
        String routingReason,
        double routingConfidence
) {
}
