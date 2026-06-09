package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.SourceDocument;
import org.springframework.boot.autoconfigure.condition.ConditionalOnExpression;
import org.springframework.stereotype.Repository;

import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/** Firestore 스토어 조건의 논리적 부정 — project-id 미설정 OR storage.mode=memory 일 때 활성화. */
@Repository
@ConditionalOnExpression(
        "'${supply-chain.firestore.project-id:}'.length() == 0 || "
                + "'memory'.equals('${supply-chain.storage.mode:firestore}')"
)
public class InMemorySourceDocumentStore implements SourceDocumentStore {

    private final Map<String, SourceDocument> store = new ConcurrentHashMap<>();

    @Override
    public SourceDocument save(SourceDocument document) {
        String id = document.id() != null ? document.id() : UUID.randomUUID().toString();
        SourceDocument saved = new SourceDocument(
                id, document.title(), document.bodyText(), document.source(),
                document.sourceType(), document.url(), document.language(), document.collectedAt()
        );
        store.put(id, saved);
        return saved;
    }

    @Override
    public Optional<SourceDocument> findById(String id) {
        return Optional.ofNullable(store.get(id));
    }
}
