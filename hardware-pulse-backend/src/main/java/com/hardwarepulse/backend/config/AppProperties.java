package com.hardwarepulse.backend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app")
public record AppProperties(Queue queue, Llm llm) {

    public record Queue(String unprocessed, String failed, long pollTimeoutSeconds) {
    }

    public record Llm(String baseUrl, String apiKey, String modelName, long timeoutSeconds) {
    }
}
