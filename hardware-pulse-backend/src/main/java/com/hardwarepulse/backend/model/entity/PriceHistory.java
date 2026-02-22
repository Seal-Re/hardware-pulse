package com.hardwarepulse.backend.model.entity;

import java.math.BigDecimal;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import com.hardwarepulse.backend.model.enums.Condition;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.MapsId;
import jakarta.persistence.Table;

@Entity
@Table(name = "price_history")
public class PriceHistory {

    @EmbeddedId
    private PriceHistoryId id;

    @MapsId("skuId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "sku_id", nullable = false)
    private StandardSku sku;

    @MapsId("listingId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "listing_id", nullable = false)
    private RawListing listing;

    @Column(name = "price", nullable = false, precision = 12, scale = 2)
    private BigDecimal price;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Column(name = "condition", nullable = false, columnDefinition = "condition_enum")
    private Condition condition;

    @Column(name = "is_valid", nullable = false)
    private boolean isValid;

    public PriceHistory() {
    }

    public PriceHistory(PriceHistoryId id,
                        StandardSku sku,
                        RawListing listing,
                        BigDecimal price,
                        Condition condition,
                        boolean isValid) {
        this.id = id;
        this.sku = sku;
        this.listing = listing;
        this.price = price;
        this.condition = condition;
        this.isValid = isValid;
    }

    public PriceHistoryId getId() {
        return id;
    }

    public void setId(PriceHistoryId id) {
        this.id = id;
    }

    public StandardSku getSku() {
        return sku;
    }

    public void setSku(StandardSku sku) {
        this.sku = sku;
    }

    public RawListing getListing() {
        return listing;
    }

    public void setListing(RawListing listing) {
        this.listing = listing;
    }

    public BigDecimal getPrice() {
        return price;
    }

    public void setPrice(BigDecimal price) {
        this.price = price;
    }

    public Condition getCondition() {
        return condition;
    }

    public void setCondition(Condition condition) {
        this.condition = condition;
    }

    public boolean isValid() {
        return isValid;
    }

    public void setValid(boolean valid) {
        isValid = valid;
    }
}
