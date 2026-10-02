package com.companyagent.api.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import io.swagger.v3.oas.annotations.media.Schema;

import java.util.List;
import java.util.Map;

@Schema(description = "智能客服对话响应")
public record ChatResponse(
        @Schema(description = "会话 ID", example = "conv-001")
        @JsonProperty("conv_id")
        String conversationId,
        @Schema(description = "客服回复")
        String response,
        @Schema(description = "识别出的意图", example = "billing")
        String intent,
        @Schema(description = "归一化意图组", example = "billing")
        String intentGroup,
        @Schema(description = "实际处理请求的 Agent 类型", example = "billing")
        String agentType,
        @Schema(description = "参与处理的 Agent 类型")
        List<String> agentTypes,
        @Schema(description = "主处理 Agent", example = "technical")
        String primaryAgent,
        @Schema(description = "辅助处理 Agent")
        List<String> supportingAgents,
        @Schema(description = "路由原因")
        String routingReason,
        @Schema(description = "路由置信度")
        double routingConfidence,
        @Schema(description = "是否需要转人工")
        boolean escalated,
        @Schema(description = "处理耗时，单位毫秒", example = "253")
        long latencyMs,
        @Schema(description = "是否使用了知识库")
        boolean knowledgeUsed,
        @Schema(description = "回答校验是否通过")
        boolean verified,
        @Schema(description = "回答是否基于上下文")
        boolean grounded,
        @Schema(description = "结构化实体")
        Map<String, List<String>> entities,
        @Schema(description = "意图识别置信度")
        double intentConfidence,
        @Schema(description = "意图识别来源分数")
        Map<String, Double> intentSourceScores
) {
}
