package com.companyagent.llm;

import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class SpringAiLlmGatewayTest {

    @Test
    void responseTextSkipsThinkingBlocks() {
        Generation thinking = new Generation(AssistantMessage.builder()
                .content("").properties(Map.of("signature", "sig")).build());
        Generation redacted = new Generation(AssistantMessage.builder()
                .properties(Map.of("data", "encrypted")).build());
        Generation answer = new Generation(new AssistantMessage("{\"intent\":\"billing\"}"));

        ChatResponse response = new ChatResponse(List.of(thinking, redacted, answer));

        assertEquals("{\"intent\":\"billing\"}", SpringAiLlmGateway.responseText(response));
    }

    @Test
    void responseTextHandlesEmptyResponse() {
        assertEquals("", SpringAiLlmGateway.responseText(null));
        assertEquals("", SpringAiLlmGateway.responseText(new ChatResponse(List.of())));
    }
}
