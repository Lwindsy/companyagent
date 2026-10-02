package com.companyagent.knowledge;

import java.util.Map;

public record SearchResult(
        String id,
        String title,
        String content,
        double score,
        int chunk,
        Map<String, Object> metadata
) {
}
