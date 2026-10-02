package com.companyagent.agent;

import com.companyagent.llm.LlmGateway;
import com.companyagent.skill.SkillManager;

public class BillingAgent extends BaseAgent {

    public BillingAgent(LlmGateway llmGateway, SkillManager skillManager) {
        super(llmGateway, skillManager);
    }

    @Override
    public AgentType type() {
        return AgentType.BILLING;
    }

    @Override
    protected String systemPrompt() {
        return "你是账单服务专家。专注账单查询、退款申请、发票问题、订阅管理。涉及实际退款操作时说明需要人工审核。";
    }
}
