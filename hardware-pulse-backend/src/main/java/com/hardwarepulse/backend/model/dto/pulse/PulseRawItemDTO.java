package com.hardwarepulse.backend.model.dto.pulse;

import java.util.Map;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public record PulseRawItemDTO(
        @JsonAlias({"raw_title", "title"}) String title,
        @JsonProperty("price_text") String priceText,
        String snippet,
        @JsonAlias({"snapshot", "ui_snapshot"}) String uiSnapshot,
        @JsonProperty("crawled_at") String crawledAt,
        // optional deep-dive fields
        @JsonProperty("seller_info") Map<String, Object> sellerInfo,
        @JsonProperty("full_desc") String fullDesc,
        @JsonProperty("ship_from") String shipFrom,
        @JsonProperty("zhima_credit") String zhimaCredit
) {
}
