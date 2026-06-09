package com.jarvis.supplychain.dto;

import java.time.Instant;
import java.util.Objects;

/**
 * 수집된 원본 뉴스/공시/실적 발표 스크립트.
 */
public record SourceDocument(
        String id,
        String title,
        String bodyText,
        String source,
        SourceType sourceType,
        String url,
        String language,
        Instant collectedAt
) {
    public enum SourceType {
        NEWS,
        FILING,
        EARNINGS_TRANSCRIPT,
        PRESS_RELEASE
    }

    public SourceDocument {
        Objects.requireNonNull(bodyText, "bodyText");
        Objects.requireNonNull(sourceType, "sourceType");
        if (collectedAt == null) {
            collectedAt = Instant.now();
        }
    }
}
