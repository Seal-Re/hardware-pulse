package com.hardwarepulse.backend.model.entity;

import java.math.BigDecimal;

import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import com.fasterxml.jackson.databind.JsonNode;
import com.hardwarepulse.backend.model.enums.Category;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

@Entity
@Table(
        name = "standard_skus",
        uniqueConstraints = {
                @UniqueConstraint(name = "standard_skus_brand_model_uniq", columnNames = {"brand", "model_name"})
        }
)
public class StandardSku {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Column(name = "category", nullable = false, columnDefinition = "category_enum")
    private Category category;

    @Column(name = "brand", nullable = false)
    private String brand;

    @Column(name = "model_name", nullable = false)
    private String modelName;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "key_specs", nullable = false, columnDefinition = "jsonb")
    private JsonNode keySpecs;

    @Column(name = "release_price", precision = 12, scale = 2)
    private BigDecimal releasePrice;

    public StandardSku() {
    }

    public StandardSku(Long id,
                       Category category,
                       String brand,
                       String modelName,
                       JsonNode keySpecs,
                       BigDecimal releasePrice) {
        this.id = id;
        this.category = category;
        this.brand = brand;
        this.modelName = modelName;
        this.keySpecs = keySpecs;
        this.releasePrice = releasePrice;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Category getCategory() {
        return category;
    }

    public void setCategory(Category category) {
        this.category = category;
    }

    public String getBrand() {
        return brand;
    }

    public void setBrand(String brand) {
        this.brand = brand;
    }

    public String getModelName() {
        return modelName;
    }

    public void setModelName(String modelName) {
        this.modelName = modelName;
    }

    public JsonNode getKeySpecs() {
        return keySpecs;
    }

    public void setKeySpecs(JsonNode keySpecs) {
        this.keySpecs = keySpecs;
    }

    public BigDecimal getReleasePrice() {
        return releasePrice;
    }

    public void setReleasePrice(BigDecimal releasePrice) {
        this.releasePrice = releasePrice;
    }
}
