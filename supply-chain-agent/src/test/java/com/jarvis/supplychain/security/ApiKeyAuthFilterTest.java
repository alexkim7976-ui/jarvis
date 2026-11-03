package com.jarvis.supplychain.security;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.io.IOException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class ApiKeyAuthFilterTest {

    @Test
    void constructorRejectsMissingOrBlankKeys() {
        assertThrows(IllegalStateException.class, () -> new ApiKeyAuthFilter(null));
        assertThrows(IllegalStateException.class, () -> new ApiKeyAuthFilter("  "));
    }

    @Test
    void healthEndpointIsExcludedFromFiltering() {
        ApiKeyAuthFilter filter = new ApiKeyAuthFilter("secret");
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setRequestURI("/api/v1/supply-chain/health");

        assertTrue(filter.shouldNotFilter(request));

        request.setRequestURI("/api/v1/supply-chain/analyses/1");
        assertFalse(filter.shouldNotFilter(request));
    }

    @Test
    void matchingKeyPassesRequestToChain() throws Exception {
        ApiKeyAuthFilter filter = new ApiKeyAuthFilter(" secret ");
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-API-Key", "secret");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilterInternal(request, response, chain);

        verify(chain).doFilter(request, response);
        assertEquals(200, response.getStatus());
        assertTrue(response.getContentAsString().isEmpty());
    }

    @Test
    void missingOrIncorrectKeyReturnsGenericUnauthorizedJson() throws Exception {
        ApiKeyAuthFilter filter = new ApiKeyAuthFilter("secret");

        assertUnauthorized(filter, null);
        assertUnauthorized(filter, "incorrect");
    }

    private static void assertUnauthorized(ApiKeyAuthFilter filter, String key) throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        if (key != null) {
            request.addHeader("X-API-Key", key);
        }
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilterInternal(request, response, (req, res) -> {
            throw new AssertionError("Unauthorized request must not reach the filter chain");
        });

        assertEquals(401, response.getStatus());
        assertEquals("application/json", response.getContentType());
        JsonNode body = new ObjectMapper().readTree(response.getContentAsString());
        assertFalse(body.get("success").asBoolean());
        assertEquals("Unauthorized", body.get("message").asText());
    }
}
