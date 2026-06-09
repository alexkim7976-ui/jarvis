package com.jarvis.supplychain.dto;

import java.time.Instant;
import java.util.List;

/**
 * Gemini 분석 결과 — Firestore 적재 및 Flutter 동기화용 DTO.
 */
public record SupplyChainAnalysisResult(
        String id,
        String sourceDocumentId,
        String targetBigTech,
        String headlineSummary,
        int impactScore,
        List<CausalLink> causalChain,
        List<ValueChainImpact> valueChainImpacts,
        String reasoningBriefing,
        String modelUsed,
        double confidence,
        Instant analyzedAt
) {}
