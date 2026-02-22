package com.hardwarepulse.backend.model.dto.pulse;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public record PulseRawBatchDTO(
        String keyword,
        @JsonAlias({"source"}) String platform,
        @JsonProperty("items") List<PulseRawItemDTO> items
) {
}
