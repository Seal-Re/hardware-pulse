package com.hardwarepulse.backend.model.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.math.BigDecimal;

/**
 * Mirrors the Phase-1 crawler contract pushed to Redis.
 */
public record RawDataDTO(
        String platform,
        @JsonProperty("external_id") String externalId,
        @JsonProperty("raw_title") String rawTitle,
        @JsonProperty("raw_price") BigDecimal rawPrice,
        @JsonProperty("seller_info") SellerInfoDTO sellerInfo,
        @JsonProperty("raw_html_snapshot") String rawHtmlSnapshot,
        @JsonProperty("crawled_at") String crawledAt
) {
}
