package com.jarvis.supplychain.dto;

/**
 * 인과관계 체인의 한 단계.
 * 예: "Google TPU 투자 확대" → "HBM/첨단 패키징 수요" → "한미반도체 장비 모멘텀"
 */
public record CausalLink(
        int step,
        String event,
        String mechanism,
        String downstreamEffect
) {}
