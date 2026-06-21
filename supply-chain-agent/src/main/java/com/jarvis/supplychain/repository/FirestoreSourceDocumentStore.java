package com.jarvis.supplychain.repository;

import com.google.cloud.firestore.Firestore;
import com.jarvis.supplychain.dto.SourceDocument;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.stereotype.Repository;

import java.util.HashMap;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ExecutionException;

@Repository
@ConditionalOnExpression(
        "'${supply-chain.firestore.project-id:}'.length() > 0 && "
                + "!'memory'.equals('${supply-chain.storage.mode:firestore}')"
)
public class FirestoreSourceDocumentStore implements SourceDocumentStore {

    private final Firestore firestore;
    private final String collection;

    public FirestoreSourceDocumentStore(
            Firestore firestore,
            @Value("${supply-chain.firestore.collection-source-documents}") String collection
    ) {
        this.firestore = firestore;
        this.collection = collection;
    }

    @Override
    public SourceDocument save(SourceDocument document) {
        String id = document.id() != null ? document.id() : UUID.randomUUID().toString();
        SourceDocument saved = new SourceDocument(
                id, document.title(), document.bodyText(), document.source(),
                document.sourceType(), document.url(), document.language(), document.collectedAt()
        );
        Map<String, Object> map = new HashMap<>();
        map.put("id", saved.id());
        map.put("title", saved.title());
        map.put("bodyText", saved.bodyText());
        map.put("source", saved.source());
        map.put("sourceType", saved.sourceType().name());
        map.put("url", saved.url());
        map.put("language", saved.language());
        map.put("collectedAt", saved.collectedAt().toString());
        try {
            firestore.collection(collection).document(id).set(map).get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Firestore write interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Firestore write failed", e);
        }
        return saved;
    }

    @Override
    public Optional<SourceDocument> findById(String id) {
        try {
            var snap = firestore.collection(collection).document(id).get().get();
            if (!snap.exists()) {
                return Optional.empty();
            }
            Map<String, Object> d = snap.getData();
            return Optional.of(new SourceDocument(
                    snap.getId(),
                    (String) d.get("title"),
                    (String) d.get("bodyText"),
                    (String) d.get("source"),
                    safeEnum(SourceDocument.SourceType.class, (String) d.get("sourceType"),
                            SourceDocument.SourceType.NEWS),
                    (String) d.get("url"),
                    (String) d.get("language"),
                    java.time.Instant.parse((String) d.get("collectedAt"))
            ));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Firestore read interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Firestore read failed", e);
        }
    }

    /** 신뢰할 수 없는 저장소 문자열을 enum 으로 안전 변환 — 미정의 값/널은 폴백으로 흡수(DoS 차단). */
    private static <E extends Enum<E>> E safeEnum(Class<E> type, String raw, E fallback) {
        if (raw == null) {
            return fallback;
        }
        try {
            return Enum.valueOf(type, raw.trim());
        } catch (IllegalArgumentException e) {
            return fallback;
        }
    }
}
