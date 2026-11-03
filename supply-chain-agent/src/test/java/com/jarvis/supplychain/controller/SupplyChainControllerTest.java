package com.jarvis.supplychain.controller;

import com.jarvis.supplychain.api.AnalyzeDocumentRequest;
import com.jarvis.supplychain.api.AnalyzeDocumentResponse;
import com.jarvis.supplychain.api.SimulateScenarioRequest;
import com.jarvis.supplychain.dto.MultiShockSimulationPayload;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.repository.AnalysisResultStore;
import com.jarvis.supplychain.service.SupplyChainAnalysisService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
class SupplyChainControllerTest {

        private TestAnalysisService analysisService;
        private TestAnalysisResultStore resultStore;
    private SupplyChainController controller;

    @BeforeEach
    void setUp() {
                analysisService = new TestAnalysisService();
                resultStore = new TestAnalysisResultStore();
        controller = new SupplyChainController(analysisService, resultStore);
    }

    @Test
    void analyzeUsesProvidedCollectionTimeAndReturnsServiceResult() {
        Instant collectedAt = Instant.parse("2025-01-02T03:04:05Z");
        SourceDocument source = new SourceDocument(
                "doc-1", "Title", "Body", "Publisher", SourceDocument.SourceType.NEWS,
                "https://example.test/article", "en", collectedAt
        );
        SupplyChainAnalysisResult expected = result("analysis-1");
        analysisService.analysisResult = expected;

        AnalyzeDocumentResponse response = controller.analyze(new AnalyzeDocumentRequest(
                "doc-1", "Title", "Body", "Publisher", SourceDocument.SourceType.NEWS,
                "https://example.test/article", "en", collectedAt
        ));

        assertTrue(response.success());
        assertSame(expected, response.result());
        assertEquals(source, analysisService.receivedDocument);
    }

    @Test
    void analyzeSuppliesCollectionTimeWhenRequestOmitsIt() {
                analysisService.analysisResult = result("analysis-2");

        controller.analyze(new AnalyzeDocumentRequest(
                null, "Title", "Body", "Publisher", SourceDocument.SourceType.NEWS,
                null, "en", null
        ));

        assertNotNull(analysisService.receivedDocument.collectedAt());
    }

    @Test
    void simulateDelegatesScenarioAndReturnsSimulation() {
        MultiShockSimulationPayload expected = new MultiShockSimulationPayload(
                "REGULATORY", "event", "briefing", List.of(), "message"
        );
        analysisService.simulation = expected;

        MultiShockSimulationPayload response = controller.simulate(
                new SimulateScenarioRequest("hypothetical scenario")
        );

        assertSame(expected, response);
        assertEquals("hypothetical scenario", analysisService.receivedScenario);
    }

    @Test
    void getAnalysisReturnsStoredResultOrNotFound() {
        SupplyChainAnalysisResult expected = result("analysis-3");
        resultStore.analysisResult = expected;
        assertSame(expected, controller.getAnalysis("analysis-3"));

        ResponseStatusException exception = assertThrows(
                ResponseStatusException.class,
                () -> controller.getAnalysis("missing")
        );
        assertEquals(HttpStatus.NOT_FOUND, exception.getStatusCode());
    }

    @Test
    void healthReturnsServiceStatus() {
        Map<String, String> health = controller.health();

        assertEquals("UP", health.get("status"));
        assertEquals("supply-chain-agent", health.get("service"));
        assertEquals(2, health.size());
    }

    private static SupplyChainAnalysisResult result(String id) {
        return new SupplyChainAnalysisResult(
                id, "doc-1", "Anchor", "Summary", 2, List.of(), List.of(),
                "Briefing", "model", 0.8, Instant.parse("2025-01-02T03:04:05Z")
        );
    }

        private static final class TestAnalysisService extends SupplyChainAnalysisService {
                private SupplyChainAnalysisResult analysisResult;
                private MultiShockSimulationPayload simulation;
                private SourceDocument receivedDocument;
                private String receivedScenario;

                private TestAnalysisService() {
                        super(null, null, null, null, "test-model", 100);
                }

                @Override
                public SupplyChainAnalysisResult analyzeAndPersist(SourceDocument document) {
                        receivedDocument = document;
                        return analysisResult;
                }

                @Override
                public MultiShockSimulationPayload callMultiShock(String scenarioText) {
                        receivedScenario = scenarioText;
                        return simulation;
                }
        }

        private static final class TestAnalysisResultStore implements AnalysisResultStore {
                private SupplyChainAnalysisResult analysisResult;

                @Override
                public void saveAnalysis(SupplyChainAnalysisResult result) {
                        analysisResult = result;
                }

                @Override
                public Optional<SupplyChainAnalysisResult> findAnalysisById(String id) {
                        return analysisResult != null && analysisResult.id().equals(id)
                                        ? Optional.of(analysisResult)
                                        : Optional.empty();
                }
        }
}
