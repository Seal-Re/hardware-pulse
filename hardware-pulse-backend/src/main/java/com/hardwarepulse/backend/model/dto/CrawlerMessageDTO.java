package com.hardwarepulse.backend.model.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Map;

public record CrawlerMessageDTO(
        String source,
        @JsonProperty("raw_data") RawDataDTO rawData,
        Map<String, Object> meta
) {
}
