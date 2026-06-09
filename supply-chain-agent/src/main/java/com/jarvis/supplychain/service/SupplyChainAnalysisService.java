package com.jarvis.supplychain.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.genai.Client;
import com.google.genai.types.Content;
import com.google.genai.types.GenerateContentConfig;
import com.google.genai.types.GenerateContentResponse;
import com.google.genai.types.Part;
import com.jarvis.supplychain.dto.CausalLink;
import com.jarvis.supplychain.dto.GeminiAnalysisPayload;
import com.jarvis.supplychain.dto.MultiShockSimulationPayload;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.dto.ValueChainImpact;
import com.jarvis.supplychain.prompt.MultiShockPromptTemplate;
import com.jarvis.supplychain.prompt.SupplyChainPromptTemplate;
import com.jarvis.supplychain.repository.AnalysisResultStore;
import com.jarvis.supplychain.repository.SourceDocumentStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 뉴스/공시 원문 → Gemini 인과관계 분석 → Firestore 저장 파이프라인.
 */
@Service
public class SupplyChainAnalysisService {

    private static final Logger log = LoggerFactory.getLogger(SupplyChainAnalysisService.class);

    private final Client geminiClient;
    private final AnalysisResultStore analysisStore;
    private final SourceDocumentStore sourceDocumentStore;
    private final ObjectMapper objectMapper;
    private final String model;
    private final int maxInputChars;

    public SupplyChainAnalysisService(
            Client geminiClient,
            AnalysisResultStore analysisStore,
            SourceDocumentStore sourceDocumentStore,
            ObjectMapper objectMapper,
            @Value("${supply-chain.gemini.model}") String model,
            @Value("${supply-chain.gemini.max-input-chars}") int maxInputChars
    ) {
        this.geminiClient = geminiClient;
        this.analysisStore = analysisStore;
        this.sourceDocumentStore = sourceDocumentStore;
        this.objectMapper = objectMapper;
        this.model = model;
        this.maxInputChars = maxInputChars;
    }

    /**
     * 원문 문서 1건을 분석하고 Firestore에 저장한 뒤 결과 DTO를 반환한다.
     */
    public SupplyChainAnalysisResult analyzeAndPersist(SourceDocument document) {
        SourceDocument saved = sourceDocumentStore.save(document);
        String trimmedBody = trimForCost(saved.bodyText());
        GeminiAnalysisPayload payload = callGemini(saved, trimmedBody);
        SupplyChainAnalysisResult result = toResult(saved, payload);
        analysisStore.saveAnalysis(result);
        log.info("Supply chain analysis saved id={} target={} impact={}",
                result.id(), result.targetBigTech(), result.impactScore());
        return result;
    }

    /** Gemini JSON Mode 호출. */
    GeminiAnalysisPayload callGemini(SourceDocument document, String bodyText) {
        GenerateContentConfig config = GenerateContentConfig.builder()
                .systemInstruction(Content.fromParts(
                        Part.fromText(SupplyChainPromptTemplate.SYSTEM_INSTRUCTION)))
                .responseMimeType("application/json")
                .responseSchema(SupplyChainPromptTemplate.responseSchema())
                .temperature(0.2f)
                .build();

        String userPrompt = SupplyChainPromptTemplate.userPrompt(
                document.title(),
                document.source(),
                document.sourceType().name(),
                document.url(),
                document.language(),
                bodyText
        );

        GenerateContentResponse response = geminiClient.models.generateContent(
                model,
                userPrompt,
                config
        );

        String json = response.text();
        if (json == null || json.isBlank()) {
            log.warn("Gemini 빈 응답 doc={}", document.id());
            throw new IllegalStateException("Gemini 분석 결과가 비어 있습니다.");
        }

        try {
            return objectMapper.readValue(json, GeminiAnalysisPayload.class);
        } catch (Exception e) {
            // 원문(json)은 서버 로그에만 기록 — 클라이언트에는 원본 JSON/내부 스펙을 노출하지 않는다.
            log.error("Gemini JSON 파싱 실패 doc={} rawLen={} rawHead={}",
                    document.id(), json.length(),
                    json.substring(0, Math.min(500, json.length())), e);
            throw new IllegalStateException("Gemini 분석 응답을 해석하지 못했습니다.");
        }
    }

    /**
     * What-If 가상 시나리오 → Multi-Shock Gemini JSON Mode 호출.
     * PRICE_SPIKE / REGULATORY / PHYSICAL_DISRUPTION 분류 + 국내 밸류체인 영향 시뮬레이션.
     */
    public MultiShockSimulationPayload callMultiShock(String scenarioText) {
        GenerateContentConfig config = GenerateContentConfig.builder()
                .systemInstruction(Content.fromParts(
                        Part.fromText(MultiShockPromptTemplate.SYSTEM_INSTRUCTION)))
                .responseMimeType("application/json")
                .responseSchema(MultiShockPromptTemplate.responseSchema())
                .temperature(0.25f)
                .build();

        String userPrompt = MultiShockPromptTemplate.userPrompt(trimForCost(scenarioText));

        GenerateContentResponse response = geminiClient.models.generateContent(
                model,
                userPrompt,
                config
        );

        String json = response.text();
        if (json == null || json.isBlank()) {
            log.warn("Multi-Shock 빈 응답");
            throw new IllegalStateException("시뮬레이션 결과가 비어 있습니다.");
        }

        try {
            return normalizeSimulation(
                    objectMapper.readValue(json, MultiShockSimulationPayload.class));
        } catch (Exception e) {
            log.error("Multi-Shock JSON 파싱 실패 rawLen={} rawHead={}",
                    json.length(), json.substring(0, Math.min(500, json.length())), e);
            throw new IllegalStateException("시뮬레이션 응답을 해석하지 못했습니다.");
        }
    }

    /** 점수 클램프(-5..+5) + status 정규화(VICTIM/NEGATIVE → CASUALTY) 방어 처리. */
    MultiShockSimulationPayload normalizeSimulation(MultiShockSimulationPayload payload) {
        List<MultiShockSimulationPayload.SimulationResult> results =
                payload.simulationResults() == null ? List.of()
                        : payload.simulationResults().stream()
                        .map(r -> new MultiShockSimulationPayload.SimulationResult(
                                r.companyName(),
                                r.ticker(),
                                canonicalStatus(r.status()),
                                clampScore(r.expectedScore()),
                                r.factorAnalysis(),
                                r.reasoningDetail()
                        ))
                        .toList();
        return new MultiShockSimulationPayload(
                payload.shockType(),
                payload.hypotheticalEventSummary(),
                payload.macroImpactBriefing(),
                results,
                payload.jarvisBriefingMessage()
        );
    }

    private String canonicalStatus(String raw) {
        if (raw == null) {
            return "NEUTRAL";
        }
        return switch (raw.trim().toUpperCase()) {
            case "BENEFICIARY" -> "BENEFICIARY";
            case "CASUALTY", "VICTIM", "NEGATIVE" -> "CASUALTY";
            default -> "NEUTRAL";
        };
    }

    /** 긴 실적 발표 스크립트 — 앞/뒤 구간을 보존해 토큰 비용을 제한한다. */
    String trimForCost(String body) {
        if (body == null) {
            return "";
        }
        if (body.length() <= maxInputChars) {
            return body;
        }
        int head = (int) (maxInputChars * 0.65);
        int tail = maxInputChars - head;
        return body.substring(0, head)
                + "\n\n[...TRUNCATED FOR COST...]\n\n"
                + body.substring(body.length() - tail);
    }

    SupplyChainAnalysisResult toResult(SourceDocument document, GeminiAnalysisPayload payload) {
        List<CausalLink> chain = payload.causalChain() == null ? List.of()
                : payload.causalChain().stream()
                .map(l -> new CausalLink(l.step(), l.event(), l.mechanism(), l.downstreamEffect()))
                .toList();

        List<ValueChainImpact> impacts = payload.valueChainImpacts() == null ? List.of()
                : payload.valueChainImpacts().stream()
                .map(v -> new ValueChainImpact(
                        v.companyName(),
                        v.ticker(),
                        parseImpactType(v.impactType()),
                        clampScore(v.impactScore()),
                        v.rationale(),
                        v.sector()
                ))
                .toList();

        return new SupplyChainAnalysisResult(
                UUID.randomUUID().toString(),
                document.id(),
                payload.targetBigTech(),
                payload.headlineSummary(),
                clampScore(payload.impactScore()),
                chain,
                impacts,
                payload.reasoningBriefing(),
                model,
                clampConfidence(payload.confidence()),
                Instant.now()
        );
    }

    private ValueChainImpact.ImpactType parseImpactType(String raw) {
        if (raw == null) {
            return ValueChainImpact.ImpactType.NEUTRAL;
        }
        return switch (raw.trim().toUpperCase()) {
            case "BENEFICIARY" -> ValueChainImpact.ImpactType.BENEFICIARY;
            case "NEGATIVE" -> ValueChainImpact.ImpactType.NEGATIVE;
            default -> ValueChainImpact.ImpactType.NEUTRAL;
        };
    }

    private int clampScore(int score) {
        return Math.max(-5, Math.min(5, score));
    }

    private double clampConfidence(double confidence) {
        if (Double.isNaN(confidence)) {
            return 0.5;
        }
        return Math.max(0.0, Math.min(1.0, confidence));
    }
}
