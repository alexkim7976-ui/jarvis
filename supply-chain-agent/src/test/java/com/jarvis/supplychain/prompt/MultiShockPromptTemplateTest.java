package com.jarvis.supplychain.prompt;

import com.google.genai.types.Schema;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MultiShockPromptTemplateTest {

    @Test
    void systemInstructionDefinesShockTypesAndKoreanOutputDiscipline() {
        String instruction = MultiShockPromptTemplate.SYSTEM_INSTRUCTION;

        assertTrue(instruction.contains("PRICE_SPIKE"));
        assertTrue(instruction.contains("REGULATORY"));
        assertTrue(instruction.contains("PHYSICAL_DISRUPTION"));
        assertTrue(instruction.contains("KOREAN"));
        assertTrue(instruction.contains("JSON"));
    }

    @Test
    void userPromptContainsScenarioAndReplacesNullWithEmptyText() {
        String prompt = MultiShockPromptTemplate.userPrompt("Port closure disrupts shipments");
        assertTrue(prompt.contains("Port closure disrupts shipments"));
        assertTrue(prompt.contains("HYPOTHETICAL SCENARIO"));

        String emptyPrompt = MultiShockPromptTemplate.userPrompt(null);
        assertFalse(emptyPrompt.contains("null"));
        assertTrue(emptyPrompt.contains("[HYPOTHETICAL SCENARIO]\n\n"));
    }

    @Test
    void responseSchemaRequiresSimulationFieldsAndResultProperties() {
        Schema schema = MultiShockPromptTemplate.responseSchema();

        assertNotNull(schema);
        Map<String, Schema> properties = schema.properties().orElseThrow();
        assertTrue(properties.keySet().containsAll(Set.of(
                "shockType", "hypotheticalEventSummary", "macroImpactBriefing",
                "simulationResults", "jarvisBriefingMessage"
        )));
        assertEquals(5, schema.required().orElseThrow().size());

        Schema itemSchema = properties.get("simulationResults").items().orElseThrow();
        assertTrue(itemSchema.properties().orElseThrow().keySet().containsAll(Set.of(
                "companyName", "ticker", "status", "expectedScore", "factorAnalysis", "reasoningDetail"
        )));
        assertEquals(6, itemSchema.required().orElseThrow().size());
    }
}
