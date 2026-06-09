package com.jarvis.supplychain.api;

import com.jarvis.supplychain.dto.SourceDocument;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.time.Instant;

public record AnalyzeDocumentRequest(
        String id,
        String title,
        @NotBlank String bodyText,
        String source,
        @NotNull SourceDocument.SourceType sourceType,
        String url,
        String language,
        Instant collectedAt
) {}
