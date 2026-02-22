package com.hardwarepulse.backend.model.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Strict JSON output schema expected from the Worker LLM.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record HardwareSpecDTO(
        String category,
        String brand,
        String chipset,
        @JsonProperty("model_series") String modelSeries,
        String vram,
        @JsonProperty("is_valid_hardware") boolean isValidHardware,
        String condition,
        @JsonProperty("reject_reason") String rejectReason
) {
}
