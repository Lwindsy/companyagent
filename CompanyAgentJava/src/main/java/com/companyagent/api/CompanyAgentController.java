package com.companyagent.api;

import com.companyagent.agent.AgentOrchestrator;
import com.companyagent.agent.AgentRequest;
import com.companyagent.agent.AgentType;
import com.companyagent.agent.AnswerVerifier;
import com.companyagent.agent.OrchestratorResult;
import com.companyagent.api.dto.BatchDocInput;
import com.companyagent.api.dto.AdminLoginRequest;
import com.companyagent.api.dto.ChatRequest;
import com.companyagent.api.dto.ChatResponse;
import com.companyagent.api.dto.EvalRunRequest;
import com.companyagent.evaluation.EndToEndEvaluator;
import com.companyagent.intent.IntentCategory;
import com.companyagent.intent.IntentRecognizer;
import com.companyagent.intent.IntentResult;
import com.companyagent.knowledge.KnowledgeBaseService;
import com.companyagent.knowledge.SearchResult;
import com.companyagent.memory.MemoryContext;
import com.companyagent.memory.MemoryManager;
import com.companyagent.memory.MessageRole;
import com.companyagent.monitor.PerformanceMonitor;
import com.companyagent.skill.SkillManager;
import com.companyagent.tool.KnowledgeToolManager;
import com.companyagent.tool.ToolResult;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

@RestController
@Tag(name = "CompanyAgent API", description = "智能客服对话、知识库、监控和评测接口")
public class CompanyAgentController {

    private final AgentOrchestrator orchestrator;
    private final IntentRecognizer intentRecognizer;
    private final MemoryManager memoryManager;
    private final KnowledgeToolManager knowledgeToolManager;
    private final KnowledgeBaseService knowledgeBaseService;
    private final AnswerVerifier answerVerifier;
    private final PerformanceMonitor performanceMonitor;
    private final EndToEndEvaluator evaluator;
    private final SkillManager skillManager;
    private final ObjectMapper objectMapper;
    private final PrometheusMeterRegistry prometheusMeterRegistry;
    private final AdminSessionService adminSessionService;

    public CompanyAgentController(
            AgentOrchestrator orchestrator,
            IntentRecognizer intentRecognizer,
            MemoryManager memoryManager,
            KnowledgeToolManager knowledgeToolManager,
            KnowledgeBaseService knowledgeBaseService,
            AnswerVerifier answerVerifier,
            PerformanceMonitor performanceMonitor,
            EndToEndEvaluator evaluator,
            SkillManager skillManager,
            ObjectMapper objectMapper,
            PrometheusMeterRegistry prometheusMeterRegistry,
            AdminSessionService adminSessionService
    ) {
        this.orchestrator = orchestrator;
        this.intentRecognizer = intentRecognizer;
        this.memoryManager = memoryManager;
        this.knowledgeToolManager = knowledgeToolManager;
        this.knowledgeBaseService = knowledgeBaseService;
        this.answerVerifier = answerVerifier;
        this.performanceMonitor = performanceMonitor;
        this.evaluator = evaluator;
        this.skillManager = skillManager;
        this.objectMapper = objectMapper;
        this.prometheusMeterRegistry = prometheusMeterRegistry;
        this.adminSessionService = adminSessionService;
    }

    @GetMapping("/health")
    @Operation(summary = "健康检查", description = "返回服务状态和 Agent 路由统计。")
    public Map<String, Object> health() {
        return Map.of("status", "ok", "agents", orchestrator.stats());
    }

    @PostMapping("/admin/login")
    @Operation(summary = "Administrator sign in")
    public AdminSessionService.LoginResult adminLogin(@Valid @RequestBody AdminLoginRequest request) {
        return adminSessionService.login(request.username(), request.password());
    }

    @GetMapping("/admin/overview")
    @Operation(summary = "Administrator workspace overview")
    public Map<String, Object> adminOverview() {
        Map<String, Object> monitor = performanceMonitor.summary();
        return Map.of(
                "status", "ok",
                "agent_stats", orchestrator.stats(),
                "knowledge_chunks", knowledgeBaseService.docCount(),
                "alerts", monitor.getOrDefault("active_alerts", List.of())
        );
    }

    @PostMapping("/chat")
    @Operation(summary = "智能客服对话", description = "执行完整对话链路：记忆读取、知识库检索、意图识别、多 Agent 路由、回答校验和记忆写入。")
    public ChatResponse chat(@Valid @RequestBody ChatRequest request) {
        String userId = request.userIdOrDefault();
        String conversationId = request.conversationId() == null || request.conversationId().isBlank()
                ? UUID.randomUUID().toString()
                : request.conversationId();
        MemoryContext memoryContext = memoryManager.getContext(userId, conversationId, request.message());
        String memoryText = memoryContext.toPromptText(objectMapper);
        List<Map<String, String>> history = memoryContext.recentMessages().stream()
                .skip(Math.max(0, memoryContext.recentMessages().size() - 5))
                .map(m -> Map.of("role", m.role().name().toLowerCase(), "content", m.content()))
                .toList();
        IntentResult intentResult = intentRecognizer.recognize(request.message(), history);
        ToolResult<List<SearchResult>> knowledge = shouldUseKnowledge(intentResult.intent())
                ? knowledgeToolManager.searchWithRewrite(request.message(), 3)
                : new ToolResult<>(true, List.of(), "knowledge_search", null, false, 0, false);
        String knowledgeText = buildKnowledgeContext(knowledge.data());
        String fullContext = join(memoryText, knowledgeText);
        OrchestratorResult result = orchestrator.run(AgentRequest.of(request.message(), userId, conversationId, fullContext, history, intentResult));
        AnswerVerifier.VerificationResult verification = answerVerifier.verify(request.message(), result.response(), fullContext);
        boolean escalated = result.escalated() || verification.needEscalation();
        memoryManager.addMessage(userId, conversationId, MessageRole.USER, request.message());
        memoryManager.addMessage(userId, conversationId, MessageRole.ASSISTANT, result.response());
        memoryManager.updateProfile(userId, conversationId);
        return new ChatResponse(
                conversationId,
                result.response(),
                result.intent() == null ? "other" : result.intent().name().toLowerCase(),
                intentResult.intentGroup(),
                result.agentType().name().toLowerCase(),
                agentNames(result.agentTypes()),
                result.primaryAgent() == null ? null : result.primaryAgent().name().toLowerCase(Locale.ROOT),
                agentNames(result.supportingAgents()),
                result.routingReason(),
                result.routingConfidence(),
                escalated,
                result.latencyMs(),
                knowledge.success() && knowledge.data() != null && !knowledge.data().isEmpty(),
                verification.pass(),
                verification.grounded(),
                intentResult.entities(),
                round(intentResult.confidence(), 4),
                intentResult.sourceScores()
        );
    }

    @PostMapping("/search")
    @Operation(summary = "知识库检索", description = "对用户查询做查询改写、并行召回和 LLM rerank。")
    public Map<String, Object> search(
            @Parameter(description = "检索关键词或用户问题", example = "退款多久能到账") @RequestParam String query,
            @Parameter(description = "返回结果数量", example = "5") @RequestParam(name = "top_k", required = false) Integer topKSnakeCase,
            @RequestParam(name = "topK", required = false) Integer topKCamelCase) {
        int topK = topKSnakeCase != null ? topKSnakeCase : (topKCamelCase != null ? topKCamelCase : 5);
        ToolResult<List<SearchResult>> result = knowledgeToolManager.searchWithRewrite(query, topK);
        return Map.of("query", query, "results", result.data(), "reranked", result.reranked());
    }

    @PostMapping("/knowledge/add")
    @Operation(summary = "批量添加知识文档", description = "将文档切片后写入 Java 版持久化知识库。")
    public Map<String, Object> addKnowledge(@Valid @RequestBody BatchDocInput input) {
        List<Map<String, String>> docs = input.documents().stream()
                .map(d -> Map.of("title", d.title(), "content", d.content()))
                .toList();
        int added = knowledgeBaseService.addDocuments(docs);
        return Map.of("added_chunks", added, "total_chunks", knowledgeBaseService.docCount());
    }

    @PostMapping(value = "/knowledge/upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "上传知识库文件", description = "支持上传 .txt、.md、.json 文件，最大 10MB。JSON 文件格式为数组：[{\"title\":\"...\",\"content\":\"...\"}]。")
    public Map<String, Object> uploadKnowledge(@Parameter(description = "知识库文件") @RequestParam("file") MultipartFile file) throws Exception {
        if (file.getSize() > 10L * 1024 * 1024) {
            throw new IllegalArgumentException("文件大小超过 10MB 限制");
        }
        String filename = file.getOriginalFilename() == null ? "unknown" : file.getOriginalFilename();
        String text = new String(file.getBytes(), java.nio.charset.StandardCharsets.UTF_8);
        List<Map<String, String>> docs;
        if (filename.endsWith(".json")) {
            docs = parseJsonDocs(text);
        } else {
            String title = filename.contains(".") ? filename.substring(0, filename.lastIndexOf('.')) : filename;
            docs = List.of(Map.of("title", title, "content", text));
        }
        int added = knowledgeBaseService.addDocuments(docs);
        return Map.of("message", "文件 " + filename + " 导入成功", "added_chunks", added, "total_chunks", knowledgeBaseService.docCount());
    }

    @GetMapping("/knowledge/stats")
    @Operation(summary = "知识库统计", description = "返回当前知识库片段数量。")
    public Map<String, Object> knowledgeStats() {
        return Map.of("total_chunks", knowledgeBaseService.docCount());
    }

    @GetMapping("/monitor")
    @Operation(summary = "监控摘要", description = "返回 Agent 指标、工具统计、告警和优化建议。")
    public Map<String, Object> monitor() {
        return performanceMonitor.summary();
    }

    @GetMapping("/skills")
    @Operation(summary = "Skills 摘要", description = "查看当前已加载的动态 Skills，便于确认热加载结果和排查解析错误。")
    public Map<String, Object> skills() {
        return skillManager.summary();
    }

    @PostMapping("/skills/reload")
    @Operation(summary = "重新加载 Skills", description = "运行时重新扫描 Skill 目录，不需要重启服务。")
    public Map<String, Object> reloadSkills() {
        skillManager.reload();
        return skillManager.summary();
    }

    @GetMapping(value = "/metrics", produces = "text/plain; version=0.0.4; charset=utf-8")
    @Operation(summary = "Prometheus 指标", description = "返回 Prometheus 文本格式指标，兼容 Python 版 /metrics 路径。")
    public ResponseEntity<String> metrics() {
        return ResponseEntity.ok(prometheusMeterRegistry.scrape());
    }

    @PostMapping(value = "/eval/run", consumes = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "运行评测", description = "运行意图识别和对话质量评测；请求体为空时使用内置默认用例。")
    public Map<String, Object> eval(@RequestBody(required = false) EvalRunRequest request) {
        return evaluator.run(request);
    }

    private boolean shouldUseKnowledge(IntentCategory intent) {
        if (intent == null) {
            return false;
        }
        return switch (intent) {
            case QUERY,
                    COMPLAINT,
                    REQUEST,
                    TECHNICAL,
                    BILLING,
                    ACCOUNT,
                    ORDER_STATUS,
                    LOGISTICS,
                    REFUND,
                    INVOICE,
                    PAYMENT_ISSUE,
                    ACCOUNT_SECURITY,
                    TECHNICAL_LOGIN,
                    TECHNICAL_CRASH -> true;
            case GREETING, ESCALATION, HUMAN_HANDOFF, FEEDBACK, OTHER -> false;
        };
    }

    private String buildKnowledgeContext(List<SearchResult> results) {
        if (results == null || results.isEmpty()) {
            return "";
        }
        List<String> parts = new ArrayList<>();
        parts.add("[知识库检索结果]");
        for (int i = 0; i < results.size(); i++) {
            SearchResult item = results.get(i);
            parts.add((i + 1) + ". 标题: " + item.title() + "\n   相关度: " + item.score() + "\n   内容: " + item.content());
        }
        parts.add("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。");
        return String.join("\n", parts);
    }

    private String join(String left, String right) {
        if (left == null || left.isBlank()) {
            return right == null ? "" : right;
        }
        if (right == null || right.isBlank()) {
            return left;
        }
        return left + "\n\n" + right;
    }

    private List<String> agentNames(List<AgentType> agentTypes) {
        if (agentTypes == null) {
            return List.of();
        }
        return agentTypes.stream()
                .map(agentType -> agentType.name().toLowerCase(Locale.ROOT))
                .toList();
    }

    private double round(double value, int digits) {
        double factor = Math.pow(10, digits);
        return Math.round(value * factor) / factor;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, String>> parseJsonDocs(String text) throws Exception {
        List<?> values = objectMapper.readValue(text, List.class);
        List<Map<String, String>> docs = new ArrayList<>();
        for (Object value : values) {
            if (value instanceof Map<?, ?> map) {
                Map<String, String> doc = new LinkedHashMap<>();
                Object title = map.containsKey("title") ? map.get("title") : "未命名文档";
                Object content = map.containsKey("content") ? map.get("content") : "";
                doc.put("title", String.valueOf(title));
                doc.put("content", String.valueOf(content));
                docs.add(doc);
            }
        }
        return docs;
    }
}
