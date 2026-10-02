package com.companyagent.agent;

import com.companyagent.llm.LlmGateway;
import com.companyagent.skill.SkillManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;
import java.util.Map;

@Configuration
public class AgentConfig {

    @Bean
    Map<AgentType, List<BaseAgent>> agentPool(LlmGateway llmGateway, SkillManager skillManager) {
        return Map.of(
                AgentType.GENERAL, List.of(new GeneralAgent(llmGateway, skillManager)),
                AgentType.TECHNICAL, List.of(new TechnicalAgent(llmGateway, skillManager)),
                AgentType.BILLING, List.of(new BillingAgent(llmGateway, skillManager))
        );
    }
}
