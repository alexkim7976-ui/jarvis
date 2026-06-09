package com.jarvis.supplychain.api;

import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;

public record AnalyzeDocumentResponse(
        boolean success,
        SupplyChainAnalysisResult result,
        String message
) {
    public static AnalyzeDocumentResponse ok(SupplyChainAnalysisResult result) {
        return new AnalyzeDocumentResponse(true, result, "Analysis completed");
    }
}
