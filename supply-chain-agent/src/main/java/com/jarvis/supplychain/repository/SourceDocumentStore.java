package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.SourceDocument;

import java.util.Optional;

public interface SourceDocumentStore {
    SourceDocument save(SourceDocument document);
    Optional<SourceDocument> findById(String id);
}
