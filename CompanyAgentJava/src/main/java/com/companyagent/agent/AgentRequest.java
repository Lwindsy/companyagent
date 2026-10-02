package com.companyagent.agent;

import com.companyagent.intent.IntentCategory;
import com.companyagent.intent.IntentResult;
import com.companyagent.intent.UrgencyLevel;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public record AgentRequest(
        String message,
        String userId,
        String conversationId,
        String context,
        List<Map<String, String>> history,
        Map<String, List<String>> entities,
        IntentCategory intent,
        String intentGroup,
        UrgencyLevel urgency,
        double intentConfidence,
        String requestId
) {
    public AgentRequest withIntent(IntentResult intentResult) {
        return new AgentRequest(
                message,
                userId,
                conversationId,
                context,
                history,
                intentResult.entities(),
                intentResult.intent(),
                intentResult.intentGroup(),
                intentResult.urgency(),
                intentResult.confidence(),
                requestId
        );
    }

    public static AgentRequest of(String message, String userId, String conversationId, String context, List<Map<String, String>> history) {
        return new AgentRequest(message, userId, conversationId, context, history, Map.of(), null, null, null, 1.0, newRequestId());
    }

    public static AgentRequest of(
            String message,
            String userId,
            String conversationId,
            String context,
            List<Map<String, String>> history,
            IntentResult intentResult
    ) {
        return new AgentRequest(
                message,
                userId,
                conversationId,
                context,
                history,
                intentResult.entities(),
                intentResult.intent(),
                intentResult.intentGroup(),
                intentResult.urgency(),
                intentResult.confidence(),
                newRequestId()
        );
    }

    private static String newRequestId() {
        return UUID.randomUUID().toString().substring(0, 8);
    }
}
