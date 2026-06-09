package com.jarvis.supplychain.api;

import jakarta.validation.constraints.NotBlank;

/**
 * What-If 시나리오 시뮬레이션 요청 — 가상 시나리오 텍스트 1건.
 */
public record SimulateScenarioRequest(
        @NotBlank String scenarioText
) {}
