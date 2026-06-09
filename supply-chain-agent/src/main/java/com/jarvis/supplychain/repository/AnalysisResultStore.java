package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;

import java.util.Optional;

public interface AnalysisResultStore {
    void saveAnalysis(SupplyChainAnalysisResult result);
    Optional<SupplyChainAnalysisResult> findAnalysisById(String id);
}
