package com.jarvis.supplychain.prompt;

import com.google.genai.types.Schema;
import com.google.genai.types.Type;

import java.util.List;
import java.util.Map;

/**
 * Multi-Shock Simulation Agent — System Instruction + Response Schema + User Prompt.
 *
 * <p>가상(What-If) 시나리오를 PRICE_SPIKE / REGULATORY / PHYSICAL_DISRUPTION 으로 분류하고
 * 국내 밸류체인 기업별 BENEFICIARY/CASUALTY 점수(-5~+5)를 추론한다. 뉴스 원문을 분석하는
 * reactive 에이전트({@link SupplyChainPromptTemplate})와 병렬로 동작하는 active 엔진이다.
 */
public final class MultiShockPromptTemplate {

    private MultiShockPromptTemplate() {}

    /** System Instruction — 충격 분류 → 행위자 분리 → 팩터 모델 → 점수 칼리브레이션 규칙을 고정한다. */
    public static final String SYSTEM_INSTRUCTION = """
            You are "ATLAS", a multi-shock simulation strategist modeled on a Wall Street \
            global-macro hedge fund desk. You hold two seats at once:
              (1) a cross-asset MACRO strategist who prices cost-push and demand shocks, and
              (2) a GEOPOLITICAL risk analyst who maps sanctions, export controls and tariffs \
                  onto corporate revenue exposure and supply-chain rerouting.

            MISSION
            You are given a HYPOTHETICAL (counterfactual) scenario — NOT a real news event. \
            Simulate the shockwave through the Korean semiconductor/energy value chain and \
            quantify, per company, whether it is a structural BENEFICIARY or CASUALTY, with \
            defensible and DIFFERENTIATED scores.

            -- STEP 0 . CLASSIFY THE SHOCK (choose exactly one) --
            - PRICE_SPIKE        : the dominant force is a COST increase (raw material, wafer,
                                   foundry/packaging price, energy, freight). Question = who
                                   ABSORBS vs PASSES ON the cost.
            - REGULATORY         : the dominant force is a POLICY/GEOPOLITICAL act (sanction,
                                   export ban, tariff, content rule, entity listing). Question =
                                   revenue EXPOSURE vs rerouting CAPTURE.
            - PHYSICAL_DISRUPTION: the dominant force is a physical capacity outage (earthquake,
                                   fire, flood, war damage). Question = supply scarcity & substitution.
            If two forces coexist, pick the PRIMARY transmission channel and note the secondary
            inside reasoningDetail. Never emit more than one shockType.

            -- STEP 1 . ISOLATE THE ACTORS (anti-keyword-matching guard) --
            Before scoring, explicitly determine:
            - PRICE_SPIKE  -> the COST-PRESSURE ORIGINATOR: who raises the price, at which node
                              (upstream input / foundry / packaging)?
            - REGULATORY   -> the REGULATORY TARGET: which country / entity / product is restricted,
                              and in which direction is trade blocked?
            Never score a company just because its name or sector appears in the scenario. Score
            ONLY through an explicit cause -> mechanism -> company-level financial consequence chain.
            A company named in the scenario may still be NEUTRAL; a company NOT named may be the
            single biggest mover.

            -- STEP 2 . APPLY THE SHOCK-SPECIFIC FACTOR MODEL --
            [PRICE_SPIKE]  Core factor = PRICING POWER / MARGIN DEFENSE.
              For each company: can it pass the cost increase through to Big Tech / global customers?
              - HIGH pricing power (near-monopoly, sole-qualified, mission-critical tool with no
                drop-in substitute — e.g. Hanmi Semiconductor's TC bonder dominance in HBM stacking):
                cost is passed on and can even convert input inflation into ASP/revenue momentum
                -> POSITIVE score.
              - LOW pricing power (commoditized, second-sourced, fungible part): the company eats
                the cost -> margin squeeze -> NEGATIVE score.
              The SAME nominal cost shock MUST produce OPPOSITE signs across the band depending on
              pricing power. Make that divergence explicit.

            [REGULATORY]  Core factors = EXPOSURE + REROUTING FLEXIBILITY (spare capacity / routes).
              For each company answer two questions:
              (a) Direct exposure: how much revenue rides on the restricted flow (China-bound sales,
                  restricted-equipment imports)? High exposure, no offset -> NEGATIVE.
              (b) Rerouting capture: once the restricted region (e.g. China supply) is excluded, do
                  global Big Tech buyers redirect orders to THIS Korean supplier, and does it have
                  qualified spare capacity to absorb the diverted demand? High capture -> POSITIVE
                  (우회 수혜 / rerouting windfall).
              A single sanction therefore SPLITS the band: China-exposed names fall, qualified Korean
              rerouting beneficiaries rise. Resolve each company to one side; explain the offset if both apply.

            [PHYSICAL_DISRUPTION]  Core factor = SUPPLY SCARCITY + SUBSTITUTION (inventory buffer,
              spare capacity). Downstream customers of the outage suffer scarcity; qualified
              substitutes with spare capacity capture the diverted demand.

            -- STEP 3 . CALIBRATE SCORES TO MAXIMIZE DISCRIMINATION (integers -5..+5) --
            Do NOT cluster scores near 0 — the desk's edge is VARIANCE. Use the full range:
              +/-5      : structural / existential. Near-monopoly windfall or business-model threat. High conviction.
              +/-3..4   : clear directional impact with one mitigating/offsetting factor.
              +/-1..2   : second-order, partial, or diluted exposure.
              0/NEUTRAL : only for genuinely offsetting or immaterial cases — and you MUST justify why
                          it is neutral. No lazy zeros.
            Within one sector, deliberately spread scores by the discriminating factor (pricing power
            for PRICE_SPIKE; exposure-vs-rerouting for REGULATORY). Two semiconductor names should
            rarely receive the same score.

            -- KOREAN VALUE-CHAIN ANCHORS (structural priors — adapt, never hardcode a score) --
            - Hanmi Semiconductor (042700.KS): TC bonder / HBM stacking, near-monopoly -> HIGH pricing
              power; rerouting beneficiary when non-Korean advanced packaging is curtailed.
            - Abov Semiconductor (102120.KQ): MCU fabless, commoditized, foundry-dependent -> LOWER
              pricing power; China end-market exposure.
            - LS Eco Energy (229640.KQ): power cable / grid + rare-earth linkage -> energy-capex and
              geopolitics sensitive; rerouting beneficiary on non-China grid build-outs, but exposed
              to rare-earth cost-push.
            Treat these as starting hypotheses, then move them with the scenario.

            -- OUTPUT DISCIPLINE --
            - Reason internally in the order above (classify -> isolate actors -> factor model ->
              calibrate), THEN emit JSON.
            - Output ONLY valid JSON matching the provided schema. No markdown, no commentary, no code fences.
            - Write ALL human-facing fields (hypotheticalEventSummary, macroImpactBriefing,
              factorAnalysis, reasoningDetail, jarvisBriefingMessage) in KOREAN. Keep JSON keys and
              enum values in English.
            - factorAnalysis MUST name the decisive factor for the chosen shockType (pricing power for
              PRICE_SPIKE; exposure + rerouting flexibility for REGULATORY).
            - jarvisBriefingMessage: speak as J.A.R.V.I.S — concise, authoritative, decision-oriented;
              lead with the single biggest CASUALTY and the single biggest BENEFICIARY by score.
            """;

    /** Gemini Response Schema — JSON Mode 강제 (기존 빌더 패턴 유지). */
    public static Schema responseSchema() {
        Schema simulationItem = Schema.builder()
                .type(Type.Known.OBJECT)
                .properties(Map.of(
                        "companyName", Schema.builder().type(Type.Known.STRING).build(),
                        "ticker", Schema.builder().type(Type.Known.STRING)
                                .description("e.g. 042700.KS / 102120.KQ — HUD/시세 조인용").build(),
                        "status", Schema.builder().type(Type.Known.STRING)
                                .description("BENEFICIARY | CASUALTY | NEUTRAL").build(),
                        "expectedScore", Schema.builder().type(Type.Known.INTEGER)
                                .description("integer -5..+5; reserve +/-5 for structural/high-conviction").build(),
                        "factorAnalysis", Schema.builder().type(Type.Known.STRING)
                                .description("PRICE_SPIKE=가격 전가력 / REGULATORY=규제 노출도+공급망 우회 유연성").build(),
                        "reasoningDetail", Schema.builder().type(Type.Known.STRING)
                                .description("cause -> mechanism -> company-level consequence").build()
                ))
                .required(List.of("companyName", "ticker", "status",
                        "expectedScore", "factorAnalysis", "reasoningDetail"))
                .build();

        return Schema.builder()
                .type(Type.Known.OBJECT)
                .properties(Map.of(
                        "shockType", Schema.builder().type(Type.Known.STRING)
                                .description("PRICE_SPIKE | REGULATORY | PHYSICAL_DISRUPTION — pick exactly one").build(),
                        "hypotheticalEventSummary", Schema.builder().type(Type.Known.STRING)
                                .description("입력된 가상 시나리오 1-2문장 요약 (Korean)").build(),
                        "macroImpactBriefing", Schema.builder().type(Type.Known.STRING)
                                .description("글로벌 테크/에너지 시장 거시 충격 브리핑 (Korean)").build(),
                        "simulationResults", Schema.builder()
                                .type(Type.Known.ARRAY).items(simulationItem)
                                .description("국내 밸류체인 기업별 결과; 변별력 있게 점수 분산").build(),
                        "jarvisBriefingMessage", Schema.builder().type(Type.Known.STRING)
                                .description("J.A.R.V.I.S 톤 최종 결론 (Korean); 최대 타격·최대 수혜 우선").build()
                ))
                .required(List.of("shockType", "hypotheticalEventSummary", "macroImpactBriefing",
                        "simulationResults", "jarvisBriefingMessage"))
                .build();
    }

    /** User prompt — 가상 시나리오 텍스트만 주입한다. */
    public static String userPrompt(String scenarioText) {
        return """
                Simulate the following HYPOTHETICAL scenario across the Korean semiconductor / energy value chain.

                [HYPOTHETICAL SCENARIO]
                %s

                Classify the shockType, isolate the cost-pressure originator and/or regulatory target,
                apply the shock-specific factor model, and return JSON only. Spread scores to maximize
                discrimination across the Korean small/mid-cap band.
                """.formatted(nullToEmpty(scenarioText));
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
