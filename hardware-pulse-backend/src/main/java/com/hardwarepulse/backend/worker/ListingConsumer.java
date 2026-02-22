package com.hardwarepulse.backend.worker;

import java.time.Duration;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.hardwarepulse.backend.config.AppProperties;
import com.hardwarepulse.backend.model.dto.CrawlerMessageDTO;
import com.hardwarepulse.backend.service.pipeline.EtlPipelineService;

@Component
public class ListingConsumer {

    private static final Logger log = LoggerFactory.getLogger(ListingConsumer.class);

    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;
    private final EtlPipelineService pipeline;
    private final AppProperties props;

    public ListingConsumer(
            StringRedisTemplate redis,
            ObjectMapper objectMapper,
            EtlPipelineService pipeline,
            AppProperties props
    ) {
        this.redis = redis;
        this.objectMapper = objectMapper;
        this.pipeline = pipeline;
        this.props = props;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void start() {
        Thread worker = new Thread(this::runLoop, "listing-consumer");
        worker.setDaemon(true);
        worker.start();
        log.info("ListingConsumer started. queue={}", props.queue().unprocessed());
    }

    private void runLoop() {
        String queue = props.queue().unprocessed();
        String dlq = props.queue().failed();
        Duration timeout = Duration.ofSeconds(props.queue().pollTimeoutSeconds());

        while (!Thread.currentThread().isInterrupted()) {
            String msgJson = null;
            try {
                msgJson = redis.opsForList().rightPop(queue, timeout);
                if (msgJson == null || msgJson.isBlank()) {
                    continue; // timeout / nothing
                }

                CrawlerMessageDTO msg = objectMapper.readValue(msgJson, CrawlerMessageDTO.class);
                pipeline.process(msg);

            } catch (DataAccessResourceFailureException | org.springframework.data.redis.RedisSystemException e) {
                // Redis may restart / be killed on Android/Termux. Treat as transient and back off.
                log.warn("Redis connection issue in consumer; backing off. err={}", e.toString());

                try {
                    Thread.sleep(3000);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                }

            } catch (Exception e) {
                log.error("Failed to process queue message", e);

                // Best-effort: push the original message to DLQ for replay/manual inspection.
                try {
                    if (msgJson != null && !msgJson.isBlank()) {
                        redis.opsForList().leftPush(dlq, msgJson);
                    }
                } catch (Exception ignored) {
                    // ignore DLQ failures
                }

                try {
                    Thread.sleep(500);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                }
            }
        }
    }
}
