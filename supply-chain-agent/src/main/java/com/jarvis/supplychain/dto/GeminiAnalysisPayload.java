package com.jarvis.supplychain.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Gemini JSON Mode 응답 역직렬화용 내부 DTO.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record GeminiAnalysisPayload(
        @JsonProperty("targetBigTech") String targetBigTech,
        @JsonProperty("headlineSummary") String headlineSummary,
        @JsonProperty("impactScore") int impactScore,
        @JsonProperty("confidence") double confidence,
        @JsonProperty("causalChain") List<GeminiCausalLink> causalChain,
        @JsonProperty("valueChainImpacts") List<GeminiValueChainImpact> valueChainImpacts,
        @JsonProperty("reasoningBriefing") String reasoningBriefing
) {
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record GeminiCausalLink(
            @JsonProperty("step") int step,
            @JsonProperty("event") String event,
            @JsonProperty("mechanism") String mechanism,
            @JsonProperty("downstreamEffect") String downstreamEffect
    ) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record GeminiValueChainImpact(
            @JsonProperty("companyName") String companyName,
            @JsonProperty("ticker") String ticker,
            @JsonProperty("impactType") String impactType,
            @JsonProperty("impactScore") int impactScore,
            @JsonProperty("rationale") String rationale,
            @JsonProperty("sector") String sector
    ) {}
}
