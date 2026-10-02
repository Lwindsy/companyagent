package com.companyagent.intent;

import java.util.List;
import java.util.Map;

public record IntentResult(
        IntentCategory intent,
        double confidence,
        UrgencyLevel urgency,
        String intentGroup,
        Map<String, List<String>> entities,
        String reasoning,
        long latencyMs,
        Map<String, Double> sourceScores
) {
}
