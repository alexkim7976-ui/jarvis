package com.jarvis.supplychain.prompt;

import com.google.genai.types.Schema;
import com.google.genai.types.Type;

import java.util.List;
import java.util.Map;

/**
 * Gemini System Instruction + Response Schema + User Prompt 템플릿.
 */
public final class SupplyChainPromptTemplate {

    private SupplyChainPromptTemplate() {}

    /** System Instruction — 인과관계 추론 규칙을 고정한다. */
    public static final String SYSTEM_INSTRUCTION = """
            You are a senior supply-chain equity analyst specializing in Big Tech capex \
            and Korean semiconductor/energy equipment value chains.

            Your task: read a source document (news, filing, earnings transcript) and infer \
            multi-hop causal chains from a Big Tech / energy anchor company to Korean suppliers.

            Reasoning rules:
            1. Do NOT stop at keyword matching. Build explicit cause → mechanism → demand/capex \
               → Korean beneficiary logic.
            2. Typical chain pattern:
               Anchor event (e.g., Google increases custom AI chip/TPU capex)
               → Intermediate demand (HBM, advanced packaging, test/bonding equipment, power infra)
               → Korean value-chain beneficiary (e.g., Hanmi Semiconductor, Abov Semiconductor)
            3. Separate BENEFICIARY vs NEGATIVE vs NEUTRAL impacts. Score each -5..+5.
            4. impactScore (-5..+5) reflects overall materiality to the Korean supply chain theme, \
               not the anchor company stock move alone.
            5. If evidence is weak, lower confidence (0.0-1.0) and keep impactScore near 0.
            6. Prefer Korean domestic suppliers when relevant: Hanmi Semiconductor (042700.KS), \
               Abov Semiconductor (102120.KQ), and energy chain names such as LS Eco Energy when \
               power/grid capex is implicated.
            7. Output ONLY valid JSON matching the provided schema. No markdown, no commentary.
            """;

    /** Gemini Response Schema — JSON Mode 강제. */
    public static Schema responseSchema() {
        Schema causalLink = Schema.builder()
                .type(Type.Known.OBJECT)
                .properties(Map.of(
                        "step", Schema.builder().type(Type.Known.INTEGER).description("1-based step index").build(),
                        "event", Schema.builder().type(Type.Known.STRING).description("Observed or inferred event").build(),
                        "mechanism", Schema.builder().type(Type.Known.STRING).description("Economic/technical transmission mechanism").build(),
                        "downstreamEffect", Schema.builder().type(Type.Known.STRING).description("Next-hop effect in the chain").build()
                ))
                .required(List.of("step", "event", "mechanism", "downstreamEffect"))
                .build();

        Schema valueChainImpact = Schema.builder()
                .type(Type.Known.OBJECT)
                .properties(Map.of(
                        "companyName", Schema.builder().type(Type.Known.STRING).build(),
                        "ticker", Schema.builder().type(Type.Known.STRING).description("e.g. 042700.KS").build(),
                        "impactType", Schema.builder()
                                .type(Type.Known.STRING)
                                .description("BENEFICIARY | NEGATIVE | NEUTRAL")
                                .build(),
                        "impactScore", Schema.builder().type(Type.Known.INTEGER).description("-5 to +5").build(),
                        "rationale", Schema.builder().type(Type.Known.STRING).build(),
                        "sector", Schema.builder().type(Type.Known.STRING).build()
                ))
                .required(List.of("companyName", "ticker", "impactType", "impactScore", "rationale", "sector"))
                .build();

        return Schema.builder()
                .type(Type.Known.OBJECT)
                .properties(Map.of(
                        "targetBigTech", Schema.builder().type(Type.Known.STRING)
                                .description("Anchor company e.g. Google, LS Eco Energy").build(),
                        "headlineSummary", Schema.builder().type(Type.Known.STRING)
                                .description("One-line Korean or English headline summary").build(),
                        "impactScore", Schema.builder().type(Type.Known.INTEGER)
                                .description("Overall Korean supply-chain impact -5..+5").build(),
                        "confidence", Schema.builder().type(Type.Known.NUMBER)
                                .description("Analyst confidence 0.0-1.0").build(),
                        "causalChain", Schema.builder()
                                .type(Type.Known.ARRAY)
                                .items(causalLink)
                                .description("At least 2 hops when possible")
                                .build(),
                        "valueChainImpacts", Schema.builder()
                                .type(Type.Known.ARRAY)
                                .items(valueChainImpact)
                                .build(),
                        "reasoningBriefing", Schema.builder().type(Type.Known.STRING)
                                .description("2-4 sentence briefing for mobile UI").build()
                ))
                .required(List.of(
                        "targetBigTech", "headlineSummary", "impactScore", "confidence",
                        "causalChain", "valueChainImpacts", "reasoningBriefing"
                ))
                .build();
    }

    /** User prompt — 원문 메타데이터 + 본문. */
    public static String userPrompt(
            String title,
            String source,
            String sourceType,
            String url,
            String language,
            String bodyText
    ) {
        return """
                Analyze the following source document for Big Tech / energy → Korean value-chain effects.

                [METADATA]
                title: %s
                source: %s
                sourceType: %s
                url: %s
                language: %s

                [DOCUMENT]
                %s

                Return JSON only. Build a multi-step causalChain and list affected Korean suppliers.
                """.formatted(
                nullToEmpty(title),
                nullToEmpty(source),
                nullToEmpty(sourceType),
                nullToEmpty(url),
                nullToEmpty(language),
                nullToEmpty(bodyText)
        );
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
