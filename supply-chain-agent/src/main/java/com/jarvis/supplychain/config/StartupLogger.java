package com.jarvis.supplychain.config;

import com.jarvis.supplychain.repository.AnalysisResultStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

@Component
public class StartupLogger {

    private static final Logger log = LoggerFactory.getLogger(StartupLogger.class);

    private final Environment env;
    private final AnalysisResultStore store;

    public StartupLogger(Environment env, AnalysisResultStore store) {
        this.env = env;
        this.store = store;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        String storageMode = env.getProperty("supply-chain.storage.mode", "firestore");
        String projectId = env.getProperty("supply-chain.firestore.project-id", "");
        String model = env.getProperty("supply-chain.gemini.model", "gemini-2.5-flash");
        String storeType = store.getClass().getSimpleName();
        log.info("Supply Chain Agent ready | model={} | storage={} | store={} | firestoreProject={}",
                model, storageMode, storeType,
                projectId.isBlank() ? "(none → in-memory fallback)" : projectId);
    }
}
