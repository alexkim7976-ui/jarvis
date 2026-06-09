package com.jarvis.supplychain.config;

import com.google.cloud.firestore.Firestore;
import com.google.cloud.firestore.FirestoreOptions;
import com.google.genai.Client;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class SupplyChainConfig {

    @Bean(destroyMethod = "close")
    Client geminiClient(@Value("${supply-chain.gemini.api-key}") String apiKey) {
        if (apiKey == null || apiKey.isBlank()) {
            throw new IllegalStateException("GEMINI_API_KEY is required");
        }
        return Client.builder().apiKey(apiKey).build();
    }

    @Bean
    @ConditionalOnProperty(prefix = "supply-chain.firestore", name = "project-id")
    Firestore firestore(@Value("${supply-chain.firestore.project-id}") String projectId) {
        return FirestoreOptions.newBuilder()
                .setProjectId(projectId)
                .build()
                .getService();
    }
}
