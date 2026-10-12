package com.jarvis.supplychain.repository;

import com.jarvis.supplychain.dto.SourceDocument;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.*;

class InMemorySourceDocumentStoreTest {

    @Test
    void save_assignsIdAndCanBeRetrieved() {
        InMemorySourceDocumentStore store = new InMemorySourceDocumentStore();

        SourceDocument doc = new SourceDocument(
                null,
                "Test Title",
                "This is the body",
                "example.com",
                SourceDocument.SourceType.NEWS,
                "http://example.com",
                "en",
                Instant.now()
        );

        SourceDocument saved = store.save(doc);

        assertNotNull(saved.id(), "Saved document should have an id");
        assertEquals(doc.title(), saved.title());
        assertEquals(doc.bodyText(), saved.bodyText());

        var found = store.findById(saved.id());
        assertTrue(found.isPresent(), "Document should be retrievable by id");
        assertEquals(saved, found.get());
    }
}
