package com.hardwarepulse.backend.model.dto;

import java.util.HashMap;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonAnyGetter;
import com.fasterxml.jackson.annotation.JsonAnySetter;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class SellerInfoDTO {
    
    @JsonProperty("seller_id")
    private String sellerId;

    private String name; 

    private String location;

    @JsonProperty("ship_from")
    private String shipFrom;

    @JsonProperty("zhima_credit")
    private String zhimaCredit;
    
    private Double rating;

    // Preserve unknown/extra seller fields (e.g. full_desc) end-to-end into jsonb.
    private final Map<String, Object> extra = new HashMap<>();

    public String getSellerId() { return sellerId; }
    public void setSellerId(String sellerId) { this.sellerId = sellerId; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getLocation() { return location; }
    public void setLocation(String location) { this.location = location; }

    public String getShipFrom() { return shipFrom; }
    public void setShipFrom(String shipFrom) { this.shipFrom = shipFrom; }

    public String getZhimaCredit() { return zhimaCredit; }
    public void setZhimaCredit(String zhimaCredit) { this.zhimaCredit = zhimaCredit; }
    
    public Double getRating() { return rating; }
    public void setRating(Double rating) { this.rating = rating; }

    @JsonAnySetter
    public void putExtra(String key, Object value) {
        if (key == null) {
            return;
        }
        extra.put(key, value);
    }

    @JsonAnyGetter
    public Map<String, Object> getExtra() {
        return extra;
    }
}