package com.companyagent.llm;

import com.companyagent.config.CompanyAgentProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.messages.AssistantMessage;
import org.springframework.ai.chat.messages.Message;
import org.springframework.ai.chat.messages.SystemMessage;
import org.springframework.ai.chat.messages.UserMessage;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.chat.model.ChatResponse;
import org.springframework.ai.chat.model.Generation;
import org.springframework.ai.chat.prompt.ChatOptions;
import org.springframework.ai.chat.prompt.Prompt;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
public class SpringAiLlmGateway implements LlmGateway {

    private static final Logger log = LoggerFactory.getLogger(SpringAiLlmGateway.class);
    private static final String STARTUP_PLACEHOLDER_KEY = "companyagent-local-placeholder";
    // 与 CompanyAgentDotnet 的 ClaudeModelCapabilities 保持一致：这一代模型会先思考，且不接受自定义 temperature
    private static final List<String> CURRENT_GENERATION =
            List.of("claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7");
    private static final int THINKING_MAX_TOKENS_FLOOR = 4096;

    private final ChatModel chatModel;
    private final CompanyAgentProperties properties;
    private final String activeProfile;
    private final boolean modelCredentialsConfigured;
    private final boolean currentGenerationModel;

    public SpringAiLlmGateway(ObjectProvider<ChatModel> chatModelProvider,
                              CompanyAgentProperties properties,
                              @Value("${spring.profiles.active:anthropic}") String activeProfile,
                              @Value("${spring.ai.anthropic.api-key:}") String anthropicApiKey,
                              @Value("${spring.ai.deepseek.api-key:}") String deepSeekApiKey,
                              @Value("${spring.ai.anthropic.chat.options.model:}") String anthropicModel) {
        this.chatModel = chatModelProvider.getIfAvailable();
        this.properties = properties;
        this.activeProfile = activeProfile;
        this.modelCredentialsConfigured = credentialsConfigured(activeProfile, anthropicApiKey, deepSeekApiKey);
        this.currentGenerationModel = !clean(activeProfile).toLowerCase().contains("deepseek")
                && CURRENT_GENERATION.stream().anyMatch(clean(anthropicModel)::startsWith);
    }

    @Override
    public String chat(String systemPrompt, String userPrompt, double temperature, int maxTokens) {
        try {
            if (chatModel == null) {
                throw new IllegalStateException("Spring AI ChatModel is not configured");
            }
            if (!modelCredentialsConfigured) {
                throw new IllegalStateException("No real API key configured for active LLM profile: " + activeProfile);
            }
            List<Message> messages = new ArrayList<>();
            if (systemPrompt != null && !systemPrompt.isBlank()) {
                messages.add(new SystemMessage(clean(systemPrompt)));
            }
            messages.add(new UserMessage(clean(userPrompt)));
            ChatOptions options = ChatOptions.builder()
                    // Spring AI 不发 null，会回落到默认值 0.8，所以新模型发 API 默认值 1.0（Sonnet 5.5 只接受默认值）
                    .temperature(currentGenerationModel ? 1.0 : temperature)
                    // 思考 token 计入 max_tokens，预算太小会把答案截断
                    .maxTokens(currentGenerationModel ? Math.max(maxTokens, THINKING_MAX_TOKENS_FLOOR) : maxTokens)
                    .build();
            ChatResponse response = chatModel.call(new Prompt(messages, options));
            return responseText(response);
        } catch (Exception ex) {
            if (!properties.getLlm().isFallbackEnabled()) {
                throw ex;
            }
            log.warn("Spring AI chat failed, using deterministic fallback: {}", ex.getMessage());
            return fallback(userPrompt);
        }
    }

    /** Spring AI 把 thinking / redacted_thinking 块也映射成 Generation（带 signature / data 元数据），这里只拼接正文。 */
    static String responseText(ChatResponse response) {
        if (response == null || response.getResults() == null) {
            return "";
        }
        StringBuilder text = new StringBuilder();
        for (Generation generation : response.getResults()) {
            AssistantMessage output = generation == null ? null : generation.getOutput();
            if (output == null || output.getText() == null) {
                continue;
            }
            Map<String, Object> metadata = output.getMetadata();
            if (metadata != null && (metadata.containsKey("signature") || metadata.containsKey("data"))) {
                continue;
            }
            text.append(output.getText());
        }
        return text.toString();
    }

    private String fallback(String userPrompt) {
        String prompt = clean(userPrompt);
        if (prompt.contains("退款") || prompt.toLowerCase().contains("refund")) {
            return "根据当前知识库，退款通常需要先提交申请并等待审核。请提供订单号，我可以继续帮你判断是否需要转人工审核。";
        }
        if (prompt.contains("报错") || prompt.contains("登录") || prompt.toLowerCase().contains("error")) {
            return "我建议先确认账号状态、网络环境和错误码。如果问题持续，请提供错误码和发生时间，技术支持会进一步排查。";
        }
        if (prompt.contains("扣款") || prompt.contains("账单") || prompt.contains("发票")) {
            return "账单问题需要核对支付记录、订单号和扣款时间。请提供相关信息，涉及退款或发票开具时会进入人工审核。";
        }
        return "我已收到你的问题。当前模型服务不可用，系统返回了本地降级回复；请稍后重试或转人工处理。";
    }

    private static String clean(String value) {
        return value == null ? "" : value;
    }

    private static boolean credentialsConfigured(String activeProfile, String anthropicApiKey, String deepSeekApiKey) {
        String profile = clean(activeProfile).toLowerCase();
        if (profile.contains("deepseek")) {
            return hasRealKey(deepSeekApiKey);
        }
        return hasRealKey(anthropicApiKey);
    }

    private static boolean hasRealKey(String value) {
        String key = clean(value).trim();
        return !key.isBlank() && !STARTUP_PLACEHOLDER_KEY.equals(key);
    }
}
