package com.companyagent.skill;

import java.util.List;
import java.util.Locale;
import java.util.Map;

public record Skill(
        String name,
        String description,
        String content,
        String path,
        List<String> keywords,
        List<String> agents,
        boolean enabled
) {
    public boolean matches(String message, String agentType) {
        if (!enabled) {
            return false;
        }
        String normalizedAgent = agentType == null ? "" : agentType.toLowerCase(Locale.ROOT);
        if (!agents.isEmpty() && !agents.contains(normalizedAgent)) {
            return false;
        }
        if (keywords.isEmpty()) {
            return true;
        }
        String lowered = message == null ? "" : message.toLowerCase(Locale.ROOT);
        return keywords.stream().anyMatch(keyword -> lowered.contains(keyword.toLowerCase(Locale.ROOT)));
    }

    public String toPromptBlock(int maxChars) {
        String body = content == null ? "" : content.strip();
        if (body.length() > maxChars) {
            body = body.substring(0, maxChars).stripTrailing() + "\n...";
        }
        String desc = description == null || description.isBlank() ? "" : "\n说明: " + description;
        return "### " + name + desc + "\n" + body;
    }

    public Map<String, Object> summary() {
        return Map.of(
                "name", name,
                "description", description == null ? "" : description,
                "path", path,
                "keywords", keywords,
                "agents", agents,
                "enabled", enabled,
                "content_chars", content == null ? 0 : content.length()
        );
    }
}
