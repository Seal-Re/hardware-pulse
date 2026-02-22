package com.hardwarepulse.backend.service.pulse;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hardwarepulse.backend.config.AppProperties;
import com.hardwarepulse.backend.model.dto.CrawlerMessageDTO;
import com.hardwarepulse.backend.model.dto.RawDataDTO;
import com.hardwarepulse.backend.model.dto.SellerInfoDTO;
import com.hardwarepulse.backend.model.dto.pulse.PulseRawBatchDTO;
import com.hardwarepulse.backend.model.dto.pulse.PulseRawItemDTO;
import com.hardwarepulse.backend.model.enums.Platform;

@Service
public class PulseIngestService {

    private static final Logger log = LoggerFactory.getLogger(PulseIngestService.class);

    // Avoid per-request compilation.
    private static final Pattern PRICE_NUMBER = Pattern.compile("(\\d+(?:\\.\\d+)?)");

    private static final int MAX_ITEMS_PER_BATCH = 200;

    // Hard backpressure to prevent Redis (and thus physical RAM) from growing without bound
    // when crawler POST rate exceeds the consumer+LLM+DB throughput.
    private static final long MAX_REDIS_BACKLOG = 1000;

    // Cap large text blobs before they become Redis payloads and DB rows.
    private static final int MAX_SNAPSHOT_CHARS = 4000;

    private final SpiderSchedulerService scheduler;
    private final StringRedisTemplate redis;
    private final AppProperties props;
    private final ObjectMapper objectMapper;

    public PulseIngestService(
            SpiderSchedulerService scheduler,
            StringRedisTemplate redis,
            AppProperties props,
            ObjectMapper objectMapper
    ) {
        this.scheduler = scheduler;
        this.redis = redis;
        this.props = props;
        this.objectMapper = objectMapper;
    }

    public void ingest(PulseRawBatchDTO batch) {
        if (batch == null) {
            return;
        }
        String keyword = safe(batch.keyword());
        if (keyword.isBlank()) {
            return;
        }

        // Backpressure: if backlog is too large, reject ingestion so the crawler can slow down.
        // This protects the device even if JVM/PG memory is capped, because Redis RSS is outside JVM/PG.
        String queue = props.queue().unprocessed();
        try {
            Long len = redis.opsForList().size(queue);
            long qlen = len == null ? 0L : len.longValue();
            if (qlen >= MAX_REDIS_BACKLOG) {
                throw new BackpressureException("Redis backlog too large: " + qlen);
            }
        } catch (BackpressureException e) {
            throw e;
        } catch (Exception e) {
            // If Redis size check fails, do not block ingestion; just log.
            log.warn("Backpressure check failed; continuing. keyword={} err={}", keyword, e.toString());
        }

        // Cap batch size to avoid OOM / huge Redis messages.
        List<PulseRawItemDTO> items = batch.items() == null ? List.of() : batch.items();
        if (items.size() > MAX_ITEMS_PER_BATCH) {
            items = items.subList(0, MAX_ITEMS_PER_BATCH);
        }

        // 1) Spawn new entities from raw titles.
        List<String> titles = new ArrayList<>(items.size());
        for (PulseRawItemDTO it : items) {
            String t = it == null ? "" : safe(it.title());
            if (!t.isBlank()) {
                titles.add(t);
            }
        }
        Set<String> entities = HardwareEntityExtractor.extractFromTitles(titles);
        int spawned = 0;
        for (String e : entities) {
            if (scheduler.spawnEntityIfNew(e, 50.0)) {
                spawned++;
            }
        }

        // 2) Compute heat decay delta from dispersion signal.
        double delta = computeHeatDelta(items);

        // 3) ACK inflight and requeue keyword with updated score (atomic via Lua).
        scheduler.ackAndRequeue(keyword, delta);

        // 4) Forward raw items into the existing Redis List queue (backwards compatibility).
        long pushed = 0;
        for (PulseRawItemDTO it : items) {
            if (it == null) {
                continue;
            }
            try {
                CrawlerMessageDTO msg = toCrawlerMessage(batch, it);
                String json = objectMapper.writeValueAsString(msg);
                redis.opsForList().leftPush(queue, json);
                pushed++;
            } catch (Exception e) {
                log.warn("Failed to forward raw item to redis queue. keyword={} err={}", keyword, e.toString());
            }
        }

        log.info("Pulse ingest done. keyword={} items={} pushed={} delta={} spawned={}",
                keyword, items.size(), pushed, delta, spawned);
    }

    private CrawlerMessageDTO toCrawlerMessage(PulseRawBatchDTO batch, PulseRawItemDTO it) {
        String platform = safe(batch.platform());
        // Keep enum compatibility with existing pipeline.
        String normalizedPlatform = platform.isBlank() ? Platform.XIANYU.name() : platform.trim().toUpperCase(Locale.ROOT);

        String title = safe(it.title());
        String priceText = safe(it.priceText());
        BigDecimal price = parsePrice(priceText);

        String crawledAt = safe(it.crawledAt());
        if (crawledAt.isBlank()) {
            crawledAt = OffsetDateTime.ofInstant(Instant.now(), ZoneOffset.UTC).toString();
        }

        SellerInfoDTO seller = new SellerInfoDTO();
        seller.setSellerId("PULSE_FEEDER");

        // Preserve the original seller_info payload (nested dict) so jsonb keeps high-dimensional data.
        // Also extract a few known keys for convenience.
        Map<String, Object> sellerInfo = it.sellerInfo();
        if (sellerInfo != null && !sellerInfo.isEmpty()) {
            try {
                seller.getExtra().putAll(sellerInfo);

                // Avoid duplicating keys that are already modeled as typed fields.
                seller.getExtra().remove("seller_id");
                seller.getExtra().remove("name");
                seller.getExtra().remove("location");
                seller.getExtra().remove("ship_from");
                seller.getExtra().remove("zhima_credit");
                seller.getExtra().remove("rating");

                Object name = sellerInfo.get("name");
                if (name instanceof String s && !s.isBlank()) {
                    seller.setName(s);
                }
                Object location = sellerInfo.get("location");
                if (location instanceof String s && !s.isBlank()) {
                    seller.setLocation(s);
                }
                Object rating = sellerInfo.get("rating");
                if (rating instanceof Number n) {
                    seller.setRating(n.doubleValue());
                } else if (rating instanceof String s) {
                    try {
                        seller.setRating(Double.parseDouble(s));
                    } catch (Exception ignored) {
                        // ignore rating parse failures
                    }
                }
            } catch (Exception ignored) {
                // ignore mapping failures
            }
        }

        String snapshot = safe(it.uiSnapshot());
        if (snapshot.isBlank()) {
            snapshot = safe(it.snippet());
        }

        // Ensure per-message payload stays bounded (Redis + DB + LLM prompt all depend on this).
        if (snapshot.length() > MAX_SNAPSHOT_CHARS) {
            snapshot = snapshot.substring(0, MAX_SNAPSHOT_CHARS);
        }

        // Append deep fields into the snapshot so the LLM layer can use them even if it doesn't read seller_info.
        String fullDesc = safe(it.fullDesc());
        String shipFrom = safe(it.shipFrom());
        String zhimaCredit = safe(it.zhimaCredit());

        if ((fullDesc.isBlank() || shipFrom.isBlank() || zhimaCredit.isBlank()) && sellerInfo != null) {
            // Prefer nested seller_info in the new crawler contract.
            if (fullDesc.isBlank()) {
                Object v = sellerInfo.get("full_desc");
                if (v instanceof String s) {
                    fullDesc = safe(s);
                }
            }
            if (shipFrom.isBlank()) {
                Object v = sellerInfo.get("ship_from");
                if (v instanceof String s) {
                    shipFrom = safe(s);
                }
            }
            if (zhimaCredit.isBlank()) {
                Object v = sellerInfo.get("zhima_credit");
                if (v instanceof String s) {
                    zhimaCredit = safe(s);
                }
            }
        }

        // Mirror the extracted keys back into the typed seller fields when present.
        if (!shipFrom.isBlank()) {
            seller.setShipFrom(shipFrom);
        }
        if (!zhimaCredit.isBlank()) {
            seller.setZhimaCredit(zhimaCredit);
        }
        if (!fullDesc.isBlank() || !shipFrom.isBlank() || !zhimaCredit.isBlank()) {
            try {
                Map<String, Object> deep = new HashMap<>();
                deep.put("full_desc", fullDesc);
                deep.put("ship_from", shipFrom);
                deep.put("zhima_credit", zhimaCredit);
                String deepJson = objectMapper.writeValueAsString(deep);
                String merged = snapshot + "\n" + deepJson;
                snapshot = merged.length() > MAX_SNAPSHOT_CHARS ? merged.substring(0, MAX_SNAPSHOT_CHARS) : merged;
            } catch (Exception ignored) {
                // ignore JSON failures
            }
        }

        RawDataDTO raw = new RawDataDTO(
                normalizedPlatform,
                buildExternalId(normalizedPlatform, batch.keyword(), title, priceText, crawledAt),
                title,
                price,
                seller,
                snapshot,
                crawledAt
        );

        Map<String, Object> meta = new HashMap<>();
        meta.put("keyword", batch.keyword());
        meta.put("price_text", priceText);

        if (!shipFrom.isBlank()) {
            meta.put("ship_from", shipFrom);
        }
        if (!zhimaCredit.isBlank()) {
            meta.put("zhima_credit", zhimaCredit);
        }

        return new CrawlerMessageDTO(normalizedPlatform, raw, meta);
    }

    private static String buildExternalId(String platform, String keyword, String title, String priceText, String crawledAt) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            String s = String.join("|",
                    safe(platform),
                    safe(keyword),
                    safe(title),
                    safe(priceText),
                    safe(crawledAt)
            );
            byte[] dig = md.digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 10 && i < dig.length; i++) {
                sb.append(String.format("%02x", dig[i]));
            }
            return "PULSE_" + sb;
        } catch (Exception ignored) {
            return "PULSE_" + (System.currentTimeMillis());
        }
    }

    private static BigDecimal parsePrice(String priceText) {
        if (priceText == null || priceText.isBlank()) {
            return BigDecimal.ZERO;
        }
        String t = priceText.replace(",", " ");
        Matcher m = PRICE_NUMBER.matcher(t);
        if (!m.find()) {
            return BigDecimal.ZERO;
        }
        try {
            return new BigDecimal(m.group(1));
        } catch (Exception ignored) {
            return BigDecimal.ZERO;
        }
    }

    /**
     * Heuristic scoring:
     * - empty results => -5
     * - dispersion high => +10
     * - otherwise small positive to keep it alive
     */
    private static double computeHeatDelta(List<PulseRawItemDTO> items) {
        if (items == null || items.isEmpty()) {
            return -5.0;
        }

        List<Double> prices = new ArrayList<>(items.size());
        for (PulseRawItemDTO it : items) {
            if (it == null) {
                continue;
            }
            BigDecimal bd = parsePrice(safe(it.priceText()));
            if (bd.signum() > 0) {
                prices.add(bd.doubleValue());
            }
        }
        if (prices.size() < 3) {
            return 1.0;
        }

        double mean = 0.0;
        for (double p : prices) {
            mean += p;
        }
        mean /= prices.size();

        double var = 0.0;
        for (double p : prices) {
            double d = p - mean;
            var += d * d;
        }
        var /= prices.size();
        double std = Math.sqrt(var);
        double cv = mean <= 0.0 ? 0.0 : (std / mean);

        // Strong boost for high dispersion.
        if (cv >= 0.25) {
            return 10.0;
        }
        return 2.0;
    }

    private static String safe(String s) {
        return s == null ? "" : s.trim();
    }

    /** Thrown to signal upstream backpressure (mapped to HTTP 503). */
    public static class BackpressureException extends RuntimeException {
        public BackpressureException(String message) {
            super(message);
        }
    }
}
