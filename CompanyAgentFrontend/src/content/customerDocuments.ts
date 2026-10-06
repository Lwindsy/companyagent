import type { CustomerDocument } from '../types'

export const customerDocuments: CustomerDocument[] = [
  {
    id: 'workflow',
    label: 'Service workflow',
    eyebrow: 'Customer guide',
    title: 'How support requests are handled',
    intro: 'A plain-English guide to how a support request is understood, routed, answered, and followed up.',
    sections: [
      {
        number: '1',
        title: 'Request lifecycle',
        detail:
          'Every chat request follows a structured sequence designed to preserve context and direct the request to the right type of help.',
        items: [
          {
            title: '1.1 Context is prepared',
            detail:
              'Recent conversation messages, relevant historical summaries, and available customer preferences are gathered before a reply is drafted.',
          },
          {
            title: '1.2 The request is understood',
            detail:
              'Semantic analysis, similarity matching, and keyword patterns identify a detailed intent, a broader intent group, and useful entities such as order references or error codes.',
          },
          {
            title: '1.3 Knowledge is retrieved when useful',
            detail:
              'For business questions, the query may be refined, searched in parallel, and ranked so the most relevant approved information can inform the response.',
          },
          {
            title: '1.4 The request is routed',
            detail:
              'General questions, technical issues, and billing or account matters are directed to the most suitable specialist capability.',
          },
          {
            title: '1.5 Guidance is applied',
            detail:
              'Relevant handling guidelines are selected by topic and specialist type, then applied while the response is prepared.',
          },
          {
            title: '1.6 The conversation is updated',
            detail:
              'The reply is saved to the active conversation. Customer-profile updates happen in the background so they do not slow down the response.',
          },
        ],
      },
      {
        number: '2',
        title: 'Specialist routing',
        detail:
          'The service does not use one fixed responder for every request. It chooses the best route from the content of the message.',
        items: [
          {
            title: '2.1 Technical support',
            detail:
              'Login problems, error codes, crashes, configuration issues, and similar troubleshooting requests are handled with diagnostic steps, likely causes, and safe next actions.',
          },
          {
            title: '2.2 Billing and account support',
            detail:
              'Refunds, duplicate charges, invoices, subscriptions, payments, account changes, and account-security questions receive billing-focused handling and privacy safeguards.',
          },
          {
            title: '2.3 General support',
            detail:
              'Greetings, product questions, order or delivery questions, and requests that do not clearly belong to a specialist area receive general guidance or a clarifying question.',
          },
          {
            title: '2.4 Human handoff and urgent requests',
            detail:
              'A direct request for a person, a complaint, or an urgent situation is marked for escalation. In a production support operation, this marker can connect to a ticket, human queue, or alert workflow.',
          },
        ],
      },
      {
        number: '3',
        title: 'Requests that span more than one topic',
        detail:
          'A single message can contain more than one valid concern, such as a sign-in error and a duplicate charge.',
        items: [
          {
            title: '3.1 Primary and supporting expertise',
            detail:
              'The strongest topic becomes the primary route. A second specialist can join when its evidence is sufficiently strong, rather than leaving part of the question unanswered.',
          },
          {
            title: '3.2 Parallel handling',
            detail:
              'Relevant specialists may work at the same time. The final response identifies the primary guidance first and then includes supporting guidance.',
          },
          {
            title: '3.3 Routing visibility',
            detail:
              'The service can report its selected primary route, supporting routes, routing reason, and domain scores for operational review.',
          },
        ],
      },
      {
        number: '4',
        title: 'Reliability and fallback',
        detail: 'A specialist failure should not leave a customer without a response.',
        items: [
          {
            title: '4.1 Specialist unavailable',
            detail: 'If a requested specialist is unavailable, the request falls back to general support.',
          },
          {
            title: '4.2 Specialist execution fails',
            detail:
              'If technical or billing handling fails, general support provides a safe fallback response.',
          },
          {
            title: '4.3 Model or knowledge-service failure',
            detail:
              'The customer receives an understandable retry or contact-support message instead of a raw system error.',
          },
        ],
      },
      {
        number: '5',
        title: 'Conversation memory and continuity',
        detail:
          'Memory keeps a conversation useful without allowing an ever-growing transcript to make responses slow or unfocused.',
        items: [
          {
            title: '5.1 Working memory',
            detail:
              'Recent messages are kept for the active conversation. The default working-memory limit is 20 messages.',
          },
          {
            title: '5.2 Automatic compression',
            detail:
              'When the conversation reaches 15 messages, older content is summarized. The summary is retained for continuity while the latest five messages stay immediately available.',
          },
          {
            title: '5.3 Customer profile',
            detail:
              'After each completed reply, recurring preferences, products, and issue types can be updated asynchronously to make future assistance more relevant.',
          },
        ],
      },
      {
        number: '6',
        title: 'Knowledge and handling guidance',
        detail: 'Business knowledge and handling guidance play different roles in a reliable answer.',
        items: [
          {
            title: '6.1 Knowledge retrieval',
            detail:
              'Business questions can retrieve relevant policies, delivery information, membership rules, technical guidance, and other approved material before an answer is generated.',
          },
          {
            title: '6.2 When retrieval is skipped',
            detail:
              'Greetings, feedback, handoff requests, and unclear requests do not trigger unnecessary retrieval, reducing irrelevant context and cost.',
          },
          {
            title: '6.3 Dynamic handling guidance',
            detail:
              'Guidance defines how support should handle a situation: what to clarify, when to escalate, what not to promise, and how to protect sensitive information.',
          },
          {
            title: '6.4 Reloading guidance',
            detail:
              'Operational guidance can be refreshed without restarting the service, allowing procedures to evolve safely.',
          },
        ],
      },
      {
        number: '7',
        title: 'Quality monitoring and evaluation',
        detail: 'Support quality is observed continuously and can be evaluated end to end.',
        items: [
          {
            title: '7.1 Live monitoring',
            detail:
              'Success rate, latency, tool reliability, and consecutive failures are collected at regular intervals. Underperforming routes can receive a lower selection score.',
          },
          {
            title: '7.2 End-to-end evaluation',
            detail:
              'Evaluation cases measure intent accuracy, run real orchestration, assess relevance, accuracy, completeness, and helpfulness, then compare results with a saved baseline.',
          },
          {
            title: '7.3 Practical outcome',
            detail:
              'The service can identify regressions and produce targeted recommendations instead of only checking whether a chat endpoint responds.',
          },
        ],
      },
    ],
  },
  {
    id: 'technical',
    label: 'Technical highlights',
    eyebrow: 'Technical guide',
    title: 'Technical capabilities at a glance',
    intro:
      'A hierarchical overview of the architecture, reliability measures, and evaluation capabilities behind the support experience.',
    sections: [
      {
        number: '1',
        title: 'End-to-end architecture',
        detail:
          'A request moves through context retrieval, intent recognition, optional knowledge retrieval, specialist routing, response generation, memory updates, and performance feedback.',
        items: [
          {
            title: '1.1 Context layer',
            detail:
              'Working memory, episodic history, and a customer profile provide continuity across messages.',
          },
          {
            title: '1.2 Decision layer',
            detail:
              'Intent recognition determines the detailed intent, intent group, entities, urgency, and whether knowledge retrieval is appropriate.',
          },
          {
            title: '1.3 Response layer',
            detail:
              'The orchestrator selects one or more specialist capabilities, applies relevant guidance, and generates the answer using the prepared context.',
          },
          {
            title: '1.4 Improvement layer',
            detail:
              'Monitoring and evaluation feed observed quality back into route selection and operational review.',
          },
        ],
      },
      {
        number: '2',
        title: 'Three-signal intent recognition',
        detail:
          'Intent detection combines complementary signals instead of depending on a single keyword list or model call.',
        items: [
          {
            title: '2.1 Semantic understanding',
            detail:
              'A language model interprets the message and recent history using examples for the supported intents.',
          },
          {
            title: '2.2 Similarity matching',
            detail: 'Embedding similarity compares the message with representative intent examples.',
          },
          {
            title: '2.3 Pattern fallback',
            detail:
              'Keywords and regular-expression patterns provide a dependable fallback for explicit signals such as error codes or refund terms.',
          },
          {
            title: '2.4 Structured output',
            detail:
              'The result includes detailed intent, normalized intent group, confidence, urgency, entities, reasoning, source scores, and processing time.',
          },
        ],
      },
      {
        number: '3',
        title: 'Knowledge retrieval and tool reliability',
        detail:
          'The retrieval path is designed to improve answer grounding while remaining usable if a dependency is unavailable.',
        items: [
          {
            title: '3.1 Query refinement and parallel recall',
            detail:
              'A business query can be rewritten into focused subqueries, retrieved in parallel from the knowledge base, merged, deduplicated, and reranked.',
          },
          {
            title: '3.2 Grounded context',
            detail:
              'Top-ranked results are added to the response context so answers can follow approved business information rather than rely only on general model knowledge.',
          },
          {
            title: '3.3 Safe fallback',
            detail:
              'Timeouts, failures, and circuit-breaker states return an explanatory fallback result. The conversation can continue instead of failing abruptly.',
          },
          {
            title: '3.4 Operational interfaces',
            detail:
              'Dedicated search, knowledge-management, and status interfaces make retrieval behavior inspectable and maintainable.',
          },
        ],
      },
      {
        number: '4',
        title: 'Three-tier memory management',
        detail: 'Different information belongs in different memory layers.',
        items: [
          {
            title: '4.1 Working memory',
            detail:
              'Fast, short-lived storage keeps the most recent conversation turns available to the active request.',
          },
          {
            title: '4.2 Episodic memory',
            detail:
              'Compressed conversation summaries are retained for semantic retrieval when older context becomes relevant again.',
          },
          {
            title: '4.3 Customer profile',
            detail:
              'Asynchronous profile extraction captures recurring preferences, products, and problem patterns without delaying the current reply.',
          },
          {
            title: '4.4 Controlled context size',
            detail:
              'Automatic summarization preserves the important history while keeping prompts bounded and efficient.',
          },
        ],
      },
      {
        number: '5',
        title: 'Multi-specialist orchestration',
        detail:
          'The orchestration layer selects, combines, and safely falls back between specialist capabilities.',
        items: [
          {
            title: '5.1 Available routes',
            detail:
              'General support, technical support, billing and account support, and escalation are distinct routes with their own handling goals.',
          },
          {
            title: '5.2 Score-based selection',
            detail:
              'Domain evidence and operational health contribute to route selection. The strongest route is primary; qualifying specialist routes can support it.',
          },
          {
            title: '5.3 Parallel execution',
            detail:
              'Cross-domain requests can be handled concurrently and returned as a clearly structured primary-plus-supporting response.',
          },
          {
            title: '5.4 Escalation and fallback',
            detail:
              'Critical urgency or a handoff request triggers escalation marking. Unavailable or failed specialists fall back to general support.',
          },
        ],
      },
      {
        number: '6',
        title: 'Dynamic guidance injection',
        detail: 'Operational procedures are loaded as modular guidance and matched to each request.',
        items: [
          {
            title: '6.1 Route-aware matching',
            detail:
              'Guidance is filtered by specialist type so technical, billing, and general-support procedures do not conflict.',
          },
          {
            title: '6.2 Keyword-aware matching',
            detail: 'Only guidance relevant to the customer’s message is added to the response instructions.',
          },
          {
            title: '6.3 Hot refresh',
            detail:
              'Guidance files can be reloaded during operation, making policy changes available without a service restart.',
          },
          {
            title: '6.4 Safety boundaries',
            detail:
              'Guidance can require information collection, prohibit unsafe promises, protect sensitive information, and define escalation conditions.',
          },
        ],
      },
      {
        number: '7',
        title: 'Monitoring feedback loop',
        detail:
          'The service measures how routes and tools perform, detects poor health, and adapts later selections.',
        items: [
          {
            title: '7.1 Collected signals',
            detail:
              'Agent and tool success rates, average latency, request totals, and consecutive failures are collected on a recurring interval.',
          },
          {
            title: '7.2 Alerts and anomaly detection',
            detail:
              'Threshold alerts and statistical anomaly checks surface service degradation for operational review.',
          },
          {
            title: '7.3 Automatic route penalty',
            detail:
              'Poor success rate or high latency adds a bounded penalty to a route, so healthier instances are favored when alternatives exist.',
          },
          {
            title: '7.4 Metrics exposure',
            detail: 'Operational metrics can be exported for monitoring systems and dashboards.',
          },
        ],
      },
      {
        number: '8',
        title: 'End-to-end evaluation',
        detail: 'The system evaluates the complete support path, not only isolated components.',
        items: [
          {
            title: '8.1 Intent evaluation',
            detail:
              'Labeled cases are used to calculate intent accuracy and macro-F1 across supported intent categories.',
          },
          {
            title: '8.2 Conversation evaluation',
            detail:
              'Single-turn and multi-turn cases run through the real orchestration path to generate actual responses.',
          },
          {
            title: '8.3 Response-quality judging',
            detail:
              'A language-model judge scores relevance, accuracy, completeness, and helpfulness on a 0–1 scale.',
          },
          {
            title: '8.4 Regression detection',
            detail:
              'Current results are compared with an earlier baseline. Meaningful declines are highlighted together with actionable recommendations.',
          },
        ],
      },
      {
        number: '9',
        title: 'Data model and module collaboration',
        detail:
          'The knowledge base, memory layers, orchestration, guidance, monitoring, and evaluation modules work as one observable service.',
        items: [
          {
            title: '9.1 Knowledge collection',
            detail:
              'Approved business documents are stored for retrieval and can be maintained through controlled knowledge-management interfaces.',
          },
          {
            title: '9.2 Episodic collection',
            detail: 'Conversation summaries support retrieval of relevant older context.',
          },
          {
            title: '9.3 Profile collection',
            detail: 'Customer-profile facts support more personalized future assistance.',
          },
          {
            title: '9.4 Traceable flow',
            detail:
              'Each major stage has an inspectable output, helping teams understand what was selected, retrieved, generated, and measured.',
          },
        ],
      },
    ],
  },
]
