package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.CausalLink;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.dto.ValueChainImpact;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class InMemoryAnalysisResultStoreTest {

    @Test
    void saveAndFindAnalysisById() {
        InMemoryAnalysisResultStore store = new InMemoryAnalysisResultStore();

        SupplyChainAnalysisResult result = new SupplyChainAnalysisResult(
            "res-1",
            "doc-1",
            "BigTech",
            "Headline",
            5,
            List.of(new CausalLink(1, "event", "mechanism", "down")),
            List.of(new ValueChainImpact("company", "TICK", ValueChainImpact.ImpactType.BENEFICIARY, 1, "rationale", "sector")),
            "reason",
            "model",
            0.9,
            Instant.now()
        );

        store.saveAnalysis(result);

        var found = store.findAnalysisById("res-1");
        assertTrue(found.isPresent());
        assertEquals(result, found.get());
    }
}
