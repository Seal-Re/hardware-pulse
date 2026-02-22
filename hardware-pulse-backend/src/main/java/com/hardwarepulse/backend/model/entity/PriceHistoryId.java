package com.hardwarepulse.backend.model.entity;

import java.io.Serializable;
import java.time.OffsetDateTime;
import java.util.Objects;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;

@Embeddable
public class PriceHistoryId implements Serializable {

    public PriceHistoryId() {
    }

    @Column(name = "time", nullable = false)
    private OffsetDateTime time;

    @Column(name = "sku_id", nullable = false)
    private Long skuId;

    @Column(name = "listing_id", nullable = false)
    private Long listingId;

    public PriceHistoryId(OffsetDateTime time, Long skuId, Long listingId) {
        this.time = time;
        this.skuId = skuId;
        this.listingId = listingId;
    }

    public OffsetDateTime getTime() {
        return time;
    }

    public void setTime(OffsetDateTime time) {
        this.time = time;
    }

    public Long getSkuId() {
        return skuId;
    }

    public void setSkuId(Long skuId) {
        this.skuId = skuId;
    }

    public Long getListingId() {
        return listingId;
    }

    public void setListingId(Long listingId) {
        this.listingId = listingId;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        PriceHistoryId that = (PriceHistoryId) o;
        return Objects.equals(time, that.time)
                && Objects.equals(skuId, that.skuId)
                && Objects.equals(listingId, that.listingId);
    }

    @Override
    public int hashCode() {
        return Objects.hash(time, skuId, listingId);
    }
}
