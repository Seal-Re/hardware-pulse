package com.hardwarepulse.backend.service.llm;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hardwarepulse.backend.config.AppProperties;
import com.hardwarepulse.backend.model.dto.HardwareSpecDTO;

@Service
public class LLMParserService {

    private static final Logger log = LoggerFactory.getLogger(LLMParserService.class);

    private static final String SYSTEM_PROMPT_TEMPLATE = """
        You are a senior hardware data engineer. Extract specifications from second-hand marketplace listings into STRICT JSON.
        
        CRITICAL RULES:
        1. brand: Translate Chinese to English (e.g., '七彩虹' -> 'Colorful', '华硕' -> 'ASUS').
        2. category: MUST be one of [GPU, CPU, SSD, RAM].
        3. model_series: The main model name (e.g., 'RTX 4090', 'i5-13600KF').
        4. chipset: The specific chip variant if applicable.
        5. vram: Extract VRAM/RAM as an integer representing GB (e.g., '24G' -> 24).
        6. condition: MUST be one of [NEW, OPEN_BOX, USED, MINING, JUNK]. 
           - MINING keywords: '锻炼', '矿'. JUNK keywords: '尸体', '点不亮'.
        7. is_valid_hardware: Set false ONLY if selling a box ('仅盒子') or cooler ('仅散热').
        8. reject_reason: Explain why invalid/junk/mining, or null.
        
        OUTPUT JSON SCHEMA (DO NOT output any extra fields like 'title'):
        {
          "brand": "String",
          "category": "String",
          "model_series": "String",
          "chipset": "String",
          "vram": "Integer",
          "condition": "String",
          "is_valid_hardware": "Boolean",
          "reject_reason": "String"
        }
        """;

    private final RestClient llmRestClient;
    private final AppProperties props;
    private final ObjectMapper objectMapper;

    public LLMParserService(RestClient llmRestClient, AppProperties props, ObjectMapper objectMapper) {
        this.llmRestClient = llmRestClient;
        this.props = props;
        this.objectMapper = objectMapper;
    }

    public HardwareSpecDTO parseListing(String rawTitle, BigDecimal price, String htmlSnippet) {
        String systemPrompt = SYSTEM_PROMPT_TEMPLATE
                .replace("{title}", rawTitle)
                .replace("{price}", price == null ? "null" : price.toPlainString());

        String safeHtml = htmlSnippet == null ? "" : htmlSnippet;
        // Keep requests bounded; the raw snapshot is stored in DB anyway.
        if (safeHtml.length() > 8000) {
            safeHtml = safeHtml.substring(0, 8000);
        }

        String userPrompt = "Title: " + rawTitle + "\n" +
                "Price: " + (price == null ? "null" : price.toPlainString()) + "\n" +
                "HTML_SNIPPET: " + safeHtml;

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("model", props.llm().modelName());
        body.put("messages", List.of(
                Map.of("role", "system", "content", systemPrompt),
                Map.of("role", "user", "content", userPrompt)
        ));
        body.put("temperature", 0.2);
        
        body.put("stream", false); 
        body.put("response_format", Map.of("type", "json_object"));

        // OpenAI-style compatible endpoint: POST /chat/completions
        JsonNode response = llmRestClient.post()
                .uri("/chat/completions")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.APPLICATION_JSON)
                .header("Authorization", "Bearer " + props.llm().apiKey())
                .body(body)
                .retrieve()
                .body(JsonNode.class);

        if (response == null) {
            throw new IllegalStateException("LLM response was null");
        }

        String content = extractContent(response);
        String cleaned = stripMarkdownCodeFence(content);
        JsonNode json = parseJsonLenient(cleaned);
        try {
            return objectMapper.treeToValue(json, HardwareSpecDTO.class);
        } catch (Exception e) {
            log.warn("Failed to map LLM JSON to HardwareSpecDTO. content={}", content);
            throw new IllegalStateException("Invalid LLM JSON schema", e);
        }
    }

    private static String stripMarkdownCodeFence(String content) {
        if (content == null) {
            return "";
        }

        String s = content.trim();
        if (s.startsWith("```") && s.endsWith("```")) {
            // Remove the first fence line (``` or ```json)
            int firstNewline = s.indexOf('\n');
            if (firstNewline >= 0) {
                s = s.substring(firstNewline + 1);
            }

            // Remove trailing fence
            int lastFence = s.lastIndexOf("```");
            if (lastFence >= 0) {
                s = s.substring(0, lastFence);
            }
        }

        return s.trim();
    }

    private static String extractContent(JsonNode response) {
        // OpenAI style: choices[0].message.content
        JsonNode contentNode = response.at("/choices/0/message/content");
        if (!contentNode.isMissingNode() && contentNode.isTextual()) {
            return contentNode.asText();
        }
        // Fallback: if response itself is JSON object for the schema
        return response.toString();
    }

    private JsonNode parseJsonLenient(String content) {
        try {
            return objectMapper.readTree(content);
        } catch (Exception ignored) {
            // Attempt to salvage JSON embedded in text
            int start = content.indexOf('{');
            int end = content.lastIndexOf('}');
            if (start >= 0 && end > start) {
                String candidate = content.substring(start, end + 1);
                try {
                    return objectMapper.readTree(candidate);
                } catch (Exception ignored2) {
                    // fall through
                }
            }
            throw new IllegalStateException("Unable to parse LLM output as JSON. content=" + content);
        }
    }
}
