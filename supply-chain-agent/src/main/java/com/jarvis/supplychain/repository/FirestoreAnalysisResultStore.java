package com.jarvis.supplychain.repository;

import com.google.cloud.firestore.DocumentReference;
import com.google.cloud.firestore.DocumentSnapshot;
import com.google.cloud.firestore.Firestore;
import com.jarvis.supplychain.dto.CausalLink;
import com.jarvis.supplychain.dto.SourceDocument;
import com.jarvis.supplychain.dto.SupplyChainAnalysisResult;
import com.jarvis.supplychain.dto.ValueChainImpact;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ExecutionException;

@Repository
@ConditionalOnExpression(
        "'${supply-chain.firestore.project-id:}'.length() > 0 && "
                + "!'memory'.equals('${supply-chain.storage.mode:firestore}')"
)
public class FirestoreAnalysisResultStore implements AnalysisResultStore {

    private final Firestore firestore;
    private final String collection;

    public FirestoreAnalysisResultStore(
            Firestore firestore,
            @Value("${supply-chain.firestore.collection-analysis-results}") String collection
    ) {
        this.firestore = firestore;
        this.collection = collection;
    }

    @Override
    public void saveAnalysis(SupplyChainAnalysisResult result) {
        write(result.id(), toMap(result));
    }

    @Override
    public Optional<SupplyChainAnalysisResult> findAnalysisById(String id) {
        try {
            DocumentSnapshot snap = firestore.collection(collection).document(id).get().get();
            if (!snap.exists()) {
                return Optional.empty();
            }
            return Optional.of(fromMap(snap.getId(), snap.getData()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Firestore read interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Firestore read failed", e);
        }
    }

    private void write(String id, Map<String, Object> data) {
        try {
            firestore.collection(collection).document(id).set(data).get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Firestore write interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Firestore write failed", e);
        }
    }

    static Map<String, Object> toMap(SupplyChainAnalysisResult r) {
        Map<String, Object> map = new HashMap<>();
        map.put("id", r.id());
        map.put("sourceDocumentId", r.sourceDocumentId());
        map.put("targetBigTech", r.targetBigTech());
        map.put("headlineSummary", r.headlineSummary());
        map.put("impactScore", r.impactScore());
        map.put("causalChain", r.causalChain().stream().map(link -> Map.of(
                "step", link.step(),
                "event", link.event(),
                "mechanism", link.mechanism(),
                "downstreamEffect", link.downstreamEffect()
        )).toList());
        map.put("valueChainImpacts", r.valueChainImpacts().stream().map(v -> Map.of(
                "companyName", v.companyName(),
                "ticker", v.ticker(),
                "impactType", v.impactType().name(),
                "impactScore", v.impactScore(),
                "rationale", v.rationale(),
                "sector", v.sector()
        )).toList());
        map.put("reasoningBriefing", r.reasoningBriefing());
        map.put("modelUsed", r.modelUsed());
        map.put("confidence", r.confidence());
        map.put("analyzedAt", r.analyzedAt().toString());
        return map;
    }

    @SuppressWarnings("unchecked")
    static SupplyChainAnalysisResult fromMap(String id, Map<String, Object> data) {
        List<Map<String, Object>> chainRaw = (List<Map<String, Object>>) data.getOrDefault("causalChain", List.of());
        List<CausalLink> chain = chainRaw.stream()
                .map(m -> new CausalLink(
                        ((Number) m.getOrDefault("step", 0)).intValue(),
                        (String) m.get("event"),
                        (String) m.get("mechanism"),
                        (String) m.get("downstreamEffect")
                ))
                .toList();

        List<Map<String, Object>> impactRaw = (List<Map<String, Object>>) data.getOrDefault("valueChainImpacts", List.of());
        List<ValueChainImpact> impacts = impactRaw.stream()
                .map(m -> new ValueChainImpact(
                        (String) m.get("companyName"),
                        (String) m.get("ticker"),
                        ValueChainImpact.ImpactType.valueOf((String) m.get("impactType")),
                        ((Number) m.getOrDefault("impactScore", 0)).intValue(),
                        (String) m.get("rationale"),
                        (String) m.get("sector")
                ))
                .toList();

        return new SupplyChainAnalysisResult(
                id,
                (String) data.get("sourceDocumentId"),
                (String) data.get("targetBigTech"),
                (String) data.get("headlineSummary"),
                ((Number) data.getOrDefault("impactScore", 0)).intValue(),
                chain,
                impacts,
                (String) data.get("reasoningBriefing"),
                (String) data.get("modelUsed"),
                ((Number) data.getOrDefault("confidence", 0.5)).doubleValue(),
                Instant.parse((String) data.get("analyzedAt"))
        );
    }
}
