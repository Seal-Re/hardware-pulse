package com.hardwarepulse.backend.model.entity;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import com.fasterxml.jackson.databind.JsonNode;
import com.hardwarepulse.backend.model.enums.Platform;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "raw_listings")
public class RawListing {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Column(name = "platform", nullable = false, columnDefinition = "platform_enum")
    private Platform platform;

    @Column(name = "external_id", nullable = false, unique = true)
    private String externalId;

    @Column(name = "raw_title", nullable = false)
    private String rawTitle;

    @Column(name = "raw_price", nullable = false, precision = 12, scale = 2)
    private BigDecimal rawPrice;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "seller_info", columnDefinition = "jsonb")
    private JsonNode sellerInfo;

    @Column(name = "raw_html_snapshot", columnDefinition = "text")
    private String rawHtmlSnapshot;

    @Column(name = "crawled_at", nullable = false)
    private OffsetDateTime crawledAt;

    public RawListing() {
    }

    public RawListing(Long id,
                      Platform platform,
                      String externalId,
                      String rawTitle,
                      BigDecimal rawPrice,
                      JsonNode sellerInfo,
                      String rawHtmlSnapshot,
                      OffsetDateTime crawledAt) {
        this.id = id;
        this.platform = platform;
        this.externalId = externalId;
        this.rawTitle = rawTitle;
        this.rawPrice = rawPrice;
        this.sellerInfo = sellerInfo;
        this.rawHtmlSnapshot = rawHtmlSnapshot;
        this.crawledAt = crawledAt;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Platform getPlatform() {
        return platform;
    }

    public void setPlatform(Platform platform) {
        this.platform = platform;
    }

    public String getExternalId() {
        return externalId;
    }

    public void setExternalId(String externalId) {
        this.externalId = externalId;
    }

    public String getRawTitle() {
        return rawTitle;
    }

    public void setRawTitle(String rawTitle) {
        this.rawTitle = rawTitle;
    }

    public BigDecimal getRawPrice() {
        return rawPrice;
    }

    public void setRawPrice(BigDecimal rawPrice) {
        this.rawPrice = rawPrice;
    }

    public JsonNode getSellerInfo() {
        return sellerInfo;
    }

    public void setSellerInfo(JsonNode sellerInfo) {
        this.sellerInfo = sellerInfo;
    }

    public String getRawHtmlSnapshot() {
        return rawHtmlSnapshot;
    }

    public void setRawHtmlSnapshot(String rawHtmlSnapshot) {
        this.rawHtmlSnapshot = rawHtmlSnapshot;
    }

    public OffsetDateTime getCrawledAt() {
        return crawledAt;
    }

    public void setCrawledAt(OffsetDateTime crawledAt) {
        this.crawledAt = crawledAt;
    }
}
