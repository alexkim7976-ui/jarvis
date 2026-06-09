package com.jarvis.supplychain.controller;

import com.jarvis.supplychain.api.AnalyzeDocumentRequest;
import com.jarvis.supplychain.api.AnalyzeDocumentResponse;
import com.jarvis.supplychain.api.SimulateScenarioRequest;
import com.jarvis.supplychain.dto.MultiShockSimulationPayload;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.repository.AnalysisResultStore;
import com.jarvis.supplychain.service.SupplyChainAnalysisService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/supply-chain")
public class SupplyChainController {

    private final SupplyChainAnalysisService analysisService;
    private final AnalysisResultStore analysisResultStore;

    public SupplyChainController(
            SupplyChainAnalysisService analysisService,
            AnalysisResultStore analysisResultStore
    ) {
        this.analysisService = analysisService;
        this.analysisResultStore = analysisResultStore;
    }

    /** 뉴스/공시/실적 원문 → Gemini 밸류체인 분석 → 저장 */
    @PostMapping("/analyze")
    public AnalyzeDocumentResponse analyze(@Valid @RequestBody AnalyzeDocumentRequest request) {
        SourceDocument document = new SourceDocument(
                request.id(),
                request.title(),
                request.bodyText(),
                request.source(),
                request.sourceType(),
                request.url(),
                request.language(),
                request.collectedAt() != null ? request.collectedAt() : Instant.now()
        );
        SupplyChainAnalysisResult result = analysisService.analyzeAndPersist(document);
        return AnalyzeDocumentResponse.ok(result);
    }

    /** What-If 가상 시나리오 → Multi-Shock 시뮬레이션 (PRICE_SPIKE / REGULATORY / PHYSICAL_DISRUPTION) */
    @PostMapping("/simulate")
    public MultiShockSimulationPayload simulate(@Valid @RequestBody SimulateScenarioRequest request) {
        return analysisService.callMultiShock(request.scenarioText());
    }

    /** Flutter 동기화용 — 분석 결과 단건 조회 */
    @GetMapping("/analyses/{id}")
    public SupplyChainAnalysisResult getAnalysis(@PathVariable String id) {
        return analysisResultStore.findAnalysisById(id)
                .orElseThrow(() -> new ResponseStatusException(
                        HttpStatus.NOT_FOUND, "Analysis not found: " + id));
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "UP", "service", "supply-chain-agent");
    }
}
