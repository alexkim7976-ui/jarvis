package com.jarvis.supplychain.dto;

/**
 * 국내 밸류체인 기업별 영향 평가.
 */
public record ValueChainImpact(
        String companyName,
        String ticker,
        ImpactType impactType,
        int impactScore,
        String rationale,
        String sector
) {
    public enum ImpactType {
        BENEFICIARY,
        NEGATIVE,
        NEUTRAL
    }
}
