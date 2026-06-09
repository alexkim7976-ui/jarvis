package com.jarvis.supplychain.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Multi-Shock Simulation Agent의 Gemini JSON Mode 응답 역직렬화 DTO.
 *
 * <p>HUD/음성 파이프라인이 그대로 소비할 수 있도록 -5~+5 점수 + status(enum) 모양을 유지한다.
 * 점수 클램프 및 status 정규화는 {@code SupplyChainAnalysisService}에서 수행한다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record MultiShockSimulationPayload(
        @JsonProperty("shockType") String shockType,
        @JsonProperty("hypotheticalEventSummary") String hypotheticalEventSummary,
        @JsonProperty("macroImpactBriefing") String macroImpactBriefing,
        @JsonProperty("simulationResults") List<SimulationResult> simulationResults,
        @JsonProperty("jarvisBriefingMessage") String jarvisBriefingMessage
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SimulationResult(
            @JsonProperty("companyName") String companyName,
            @JsonProperty("ticker") String ticker,
            @JsonProperty("status") String status,            // BENEFICIARY | CASUALTY | NEUTRAL
            @JsonProperty("expectedScore") int expectedScore,  // clamp -5..+5 (service)
            @JsonProperty("factorAnalysis") String factorAnalysis,
            @JsonProperty("reasoningDetail") String reasoningDetail
    ) {}
}
