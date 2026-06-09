package com.jarvis.supplychain.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.genai.Client;
import com.jarvis.supplychain.dto.GeminiAnalysisPayload;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.repository.InMemoryAnalysisResultStore;
import com.jarvis.supplychain.repository.InMemorySourceDocumentStore;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
}
