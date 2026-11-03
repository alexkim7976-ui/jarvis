package com.jarvis.supplychain.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.genai.Client;
import com.jarvis.supplychain.dto.GeminiAnalysisPayload;
import com.jarvis.supplychain.dto.MultiShockSimulationPayload;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.repository.InMemoryAnalysisResultStore;
import com.jarvis.supplychain.repository.InMemorySourceDocumentStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SupplyChainAnalysisServiceTest {

    private SupplyChainAnalysisService service;

    @BeforeEach
    void setUp() {
        service = new SupplyChainAnalysisService(
                null,
                new InMemoryAnalysisResultStore(),
                new InMemorySourceDocumentStore(),
                new ObjectMapper(),
                "gemini-2.5-flash",
                100
        );
    }

    @Test
    void trimForCost_truncatesLongText() {
        String body = "A".repeat(200);
        String trimmed = service.trimForCost(body);
        assertTrue(trimmed.contains("TRUNCATED"));
        assertTrue(trimmed.length() < body.length());
                assertTrue(trimmed.startsWith("A"));
                assertTrue(trimmed.endsWith("A"));
        }

        @Test
        void trimForCost_preservesNullAndTextWithinLimit() {
                assertEquals("", service.trimForCost(null));
                assertEquals("short", service.trimForCost("short"));
    }

    @Test
    void toResult_mapsGeminiPayload() {
        SourceDocument doc = new SourceDocument(
                "doc-1", "Google AI capex", "body", "Reuters",
                SourceDocument.SourceType.NEWS, "https://x", "en", Instant.now()
        );
        GeminiAnalysisPayload payload = new GeminiAnalysisPayload(
                "Google",
                "AI chip investment rises",
                3,
                0.85,
                List.of(new GeminiAnalysisPayload.GeminiCausalLink(
                        1, "Google expands TPU capex", "More advanced packaging demand",
                        "Korean OSAT equipment orders increase"
                )),
                List.of(new GeminiAnalysisPayload.GeminiValueChainImpact(
                        "Hanmi Semiconductor", "042700.KS", "BENEFICIARY", 4,
                        "Packaging equipment exposure", "Semiconductor equipment"
                )),
                "Google capex is bullish for Hanmi Semiconductor."
        );

        SupplyChainAnalysisResult result = service.toResult(doc, payload);

        assertEquals("Google", result.targetBigTech());
        assertEquals(3, result.impactScore());
        assertEquals(1, result.causalChain().size());
        assertEquals("Hanmi Semiconductor", result.valueChainImpacts().get(0).companyName());
    }

    @Test
    void toResult_clampsScoresAndConfidenceAndHandlesNullLists() {
        SourceDocument doc = new SourceDocument(
                "doc-2", "Title", "body", "Source",
                SourceDocument.SourceType.NEWS, "https://example.test", "en", Instant.now()
        );
        GeminiAnalysisPayload payload = new GeminiAnalysisPayload(
                "Anchor", "Summary", 9, Double.NaN, null, null, "Briefing"
        );

        SupplyChainAnalysisResult result = service.toResult(doc, payload);

        assertEquals(5, result.impactScore());
        assertEquals(0.5, result.confidence());
        assertNotNull(result.causalChain());
        assertTrue(result.causalChain().isEmpty());
        assertNotNull(result.valueChainImpacts());
        assertTrue(result.valueChainImpacts().isEmpty());
    }

    @Test
    void normalizeSimulation_clampsScoresAndCanonicalizesStatuses() {
        MultiShockSimulationPayload payload = new MultiShockSimulationPayload(
                "REGULATORY", "event", "briefing",
                List.of(
                        new MultiShockSimulationPayload.SimulationResult(
                                "Company A", "A", " victim ", 8, "factor", "reason"),
                        new MultiShockSimulationPayload.SimulationResult(
                                "Company B", "B", "beneficiary", -8, "factor", "reason"),
                        new MultiShockSimulationPayload.SimulationResult(
                                "Company C", "C", "other", 0, "factor", "reason"),
                        new MultiShockSimulationPayload.SimulationResult(
                                "Company D", "D", null, 2, "factor", "reason")
                ),
                "message"
        );

        MultiShockSimulationPayload normalized = service.normalizeSimulation(payload);

        assertEquals("CASUALTY", normalized.simulationResults().get(0).status());
        assertEquals(5, normalized.simulationResults().get(0).expectedScore());
        assertEquals("BENEFICIARY", normalized.simulationResults().get(1).status());
        assertEquals(-5, normalized.simulationResults().get(1).expectedScore());
        assertEquals("NEUTRAL", normalized.simulationResults().get(2).status());
        assertEquals("NEUTRAL", normalized.simulationResults().get(3).status());
        assertFalse(normalized.simulationResults().isEmpty());
    }

    @Test
    void normalizeSimulation_treatsMissingResultsAsEmpty() {
        MultiShockSimulationPayload payload = new MultiShockSimulationPayload(
                "PRICE_SPIKE", "event", "briefing", null, "message"
        );

        assertTrue(service.normalizeSimulation(payload).simulationResults().isEmpty());
    }
}
