package com.hardwarepulse.backend.service.pipeline;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.hardwarepulse.backend.model.dto.CrawlerMessageDTO;
import com.hardwarepulse.backend.model.dto.HardwareSpecDTO;
import com.hardwarepulse.backend.model.entity.PriceHistory;
import com.hardwarepulse.backend.model.entity.PriceHistoryId;
import com.hardwarepulse.backend.model.entity.RawListing;
import com.hardwarepulse.backend.model.entity.StandardSku;
import com.hardwarepulse.backend.model.enums.Category;
import com.hardwarepulse.backend.model.enums.Condition;
import com.hardwarepulse.backend.model.enums.Platform;
import com.hardwarepulse.backend.repository.PriceHistoryRepository;
import com.hardwarepulse.backend.repository.RawListingRepository;
import com.hardwarepulse.backend.repository.StandardSkuRepository;
import com.hardwarepulse.backend.service.llm.LLMParserService;

import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import jakarta.transaction.Transactional;

@Service
public class EtlPipelineService {

    private static final Logger log = LoggerFactory.getLogger(EtlPipelineService.class);

    private final RawListingRepository rawListingRepository;
    private final StandardSkuRepository standardSkuRepository;
    private final PriceHistoryRepository priceHistoryRepository;
    private final LLMParserService llmParserService;
    private final ObjectMapper objectMapper;

    @PersistenceContext
    private EntityManager entityManager;

    public EtlPipelineService(
            RawListingRepository rawListingRepository,
            StandardSkuRepository standardSkuRepository,
            PriceHistoryRepository priceHistoryRepository,
            LLMParserService llmParserService,
            ObjectMapper objectMapper
    ) {
        this.rawListingRepository = rawListingRepository;
        this.standardSkuRepository = standardSkuRepository;
        this.priceHistoryRepository = priceHistoryRepository;
        this.llmParserService = llmParserService;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public void process(CrawlerMessageDTO msg) {
        var raw = msg.rawData();

        // Layer 1: save raw listing (immutable by convention)
        RawListing listing = rawListingRepository.findByExternalId(raw.externalId())
                .orElseGet(() -> {
                    RawListing l = new RawListing();
                    l.setPlatform(Platform.valueOf(raw.platform()));
                    l.setExternalId(raw.externalId());
                    l.setRawTitle(raw.rawTitle());
                    l.setRawPrice(raw.rawPrice() == null ? BigDecimal.ZERO : raw.rawPrice());
                    l.setSellerInfo(objectMapper.valueToTree(raw.sellerInfo()));
                    l.setRawHtmlSnapshot(raw.rawHtmlSnapshot());
                    l.setCrawledAt(parseOffsetDateTime(raw.crawledAt()));
                    return l;
                });

        listing = rawListingRepository.save(listing);

        // LLM parsing
        HardwareSpecDTO spec = llmParserService.parseListing(
                raw.rawTitle(),
                raw.rawPrice(),
                raw.rawHtmlSnapshot()
        );

        if (!spec.isValidHardware()) {
            log.info("Rejected listing externalId={} reason={} title={}",
                    raw.externalId(), spec.rejectReason(), raw.rawTitle());
            return;
        }

        // Layer 2: upsert standard SKU
        String brand = safeText(spec.brand());
        String modelName = safeText(spec.modelSeries());

        if (brand.isBlank() || modelName.isBlank()) {
            log.info("LLM returned incomplete SKU fields; marking invalid. externalId={} spec={}",
                    raw.externalId(), spec);
            return;
        }

        StandardSku sku = standardSkuRepository.findByBrandAndModelName(brand, modelName)
                .orElseGet(() -> {
                    StandardSku s = new StandardSku();
                    s.setBrand(brand);
                    s.setModelName(modelName);
                    s.setCategory(parseCategory(spec.category()));
                    s.setKeySpecs(buildKeySpecs(spec));
                    return s;
                });

        sku = standardSkuRepository.save(sku);

        // Layer 3: write price history point
        PriceHistory point = new PriceHistory();
        PriceHistoryId id = new PriceHistoryId(
                parseOffsetDateTime(raw.crawledAt()),
                sku.getId(),
                listing.getId()
        );
        point.setId(id);
        point.setSku(sku);
        point.setListing(listing);
        point.setPrice(raw.rawPrice() == null ? BigDecimal.ZERO : raw.rawPrice());
        point.setCondition(parseCondition(spec.condition()));
        point.setValid(true);

        priceHistoryRepository.save(point);

        // Hardening: ensure persistence context doesn't retain entities longer than needed.
        // This protects against future refactors that accidentally add batching.
        entityManager.clear();
    }

    private OffsetDateTime parseOffsetDateTime(String iso) {
        if (iso == null || iso.isBlank()) {
            return OffsetDateTime.now();
        }
        return OffsetDateTime.parse(iso);
    }

    private Category parseCategory(String category) {
        if (category == null) {
            return Category.GPU;
        }
        try {
            return Category.valueOf(category.trim().toUpperCase());
        } catch (Exception ignored) {
            return Category.GPU;
        }
    }

    private Condition parseCondition(String condition) {
        if (condition == null) {
            return Condition.NEW;
        }
        String normalized = condition.trim().toUpperCase();
        try {
            return Condition.valueOf(normalized);
        } catch (Exception ignored) {
            // Accept common aliases from LLM
            return switch (normalized) {
                case "OPEN-BOX", "OPENBOX" -> Condition.OPEN_BOX;
                default -> Condition.NEW;
            };
        }
    }

    private JsonNode buildKeySpecs(HardwareSpecDTO spec) {
        ObjectNode node = objectMapper.createObjectNode();
        putIfPresent(node, "chipset", spec.chipset());
        putIfPresent(node, "vram", spec.vram());
        return node;
    }

    private static void putIfPresent(ObjectNode node, String key, String value) {
        if (value != null && !value.isBlank()) {
            node.put(key, value);
        }
    }

    private static String safeText(String s) {
        return s == null ? "" : s.trim();
    }
}
