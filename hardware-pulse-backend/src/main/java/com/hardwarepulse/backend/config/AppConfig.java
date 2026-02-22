package com.hardwarepulse.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class AppConfig {

    @Bean
    public RestClient llmRestClient(AppProperties props) {
        var factory = new SimpleClientHttpRequestFactory();
        int timeoutMs = Math.toIntExact(props.llm().timeoutSeconds() * 1000);
        factory.setConnectTimeout(timeoutMs);
        factory.setReadTimeout(timeoutMs);

        return RestClient.builder()
                .baseUrl(props.llm().baseUrl())
                .requestFactory(factory)
                .build();
    }

    @Bean
    public ObjectMapper objectMapper() {
        // Register JavaTimeModule etc. to keep JSON handling predictable.
        return new ObjectMapper().findAndRegisterModules();
    }
}
