package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.stereotype.Repository;

import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/** Firestore 미설정·로컬 개발용 인메모리 저장소 (자동 폴백).
 *  Firestore 스토어 조건의 논리적 부정 — project-id 미설정 OR storage.mode=memory 일 때 활성화. */
@Repository
@ConditionalOnExpression(
        "'${supply-chain.firestore.project-id:}'.length() == 0 || "
                + "'memory'.equals('${supply-chain.storage.mode:firestore}')"
)
public class InMemoryAnalysisResultStore implements AnalysisResultStore {

    private final Map<String, SupplyChainAnalysisResult> store = new ConcurrentHashMap<>();

    @Override
    public void saveAnalysis(SupplyChainAnalysisResult result) {
        store.put(result.id(), result);
    }

    @Override
    public Optional<SupplyChainAnalysisResult> findAnalysisById(String id) {
        return Optional.ofNullable(store.get(id));
    }
}
