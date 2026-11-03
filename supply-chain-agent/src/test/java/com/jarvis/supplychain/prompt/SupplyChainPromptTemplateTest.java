package com.jarvis.supplychain.prompt;

import com.google.genai.types.Schema;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SupplyChainPromptTemplateTest {

    @Test
    void systemInstructionRequiresJsonAndSupplyChainReasoning() {
        assertTrue(SupplyChainPromptTemplate.SYSTEM_INSTRUCTION.contains("valid JSON"));
        assertTrue(SupplyChainPromptTemplate.SYSTEM_INSTRUCTION.contains("cause"));
        assertTrue(SupplyChainPromptTemplate.SYSTEM_INSTRUCTION.contains("BENEFICIARY"));
    }

    @Test
    void userPromptIncludesMetadataAndDocumentAndNormalizesNulls() {
        String prompt = SupplyChainPromptTemplate.userPrompt(
                "Quarterly results", null, "NEWS", "https://example.test", "en", "Revenue increased"
        );

        assertTrue(prompt.contains("title: Quarterly results"));
        assertTrue(prompt.contains("source: \n"));
        assertTrue(prompt.contains("sourceType: NEWS"));
        assertTrue(prompt.contains("url: https://example.test"));
        assertTrue(prompt.contains("language: en"));
        assertTrue(prompt.contains("Revenue increased"));
        assertFalse(prompt.contains("null"));
    }

    @Test
    void responseSchemaDeclaresRequiredTopLevelAndNestedFields() {
        Schema schema = SupplyChainPromptTemplate.responseSchema();

        assertNotNull(schema);
        assertEquals("OBJECT", schema.type().orElseThrow().toString());
        Map<String, Schema> properties = schema.properties().orElseThrow();
        assertTrue(properties.keySet().containsAll(java.util.Set.of(
                "targetBigTech", "headlineSummary", "impactScore", "confidence",
                "causalChain", "valueChainImpacts", "reasoningBriefing"
        )));
        assertEquals(7, schema.required().orElseThrow().size());
        assertEquals(4, properties.get("causalChain").items().orElseThrow()
                .required().orElseThrow().size());
        assertEquals(6, properties.get("valueChainImpacts").items().orElseThrow()
                .required().orElseThrow().size());
    }
}
