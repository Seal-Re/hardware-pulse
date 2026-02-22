package com.hardwarepulse.backend.service.pulse;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Objects;

import org.springframework.core.io.ClassPathResource;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

@Service
public class SpiderSchedulerService {

    public static final String TASKS_ZSET = "spider:tasks";
    public static final String INFLIGHT_ZSET = "spider:tasks:inflight";
    public static final String KNOWN_SET = "spider:entities:known";
    public static final String LAST_SCORE_HASH = "spider:tasks:last_score";

    private final StringRedisTemplate redis;
    private final DefaultRedisScript<List> moveMaxToInflightScript;
    private final DefaultRedisScript<Long> ackAndRequeueScript;
    private final DefaultRedisScript<Long> requeueStaleInflightScript;

    public SpiderSchedulerService(StringRedisTemplate redis) {
        this.redis = redis;

        this.moveMaxToInflightScript = new DefaultRedisScript<>();
        this.moveMaxToInflightScript.setResultType(List.class);

        this.ackAndRequeueScript = new DefaultRedisScript<>();
        this.ackAndRequeueScript.setResultType(Long.class);

        this.requeueStaleInflightScript = new DefaultRedisScript<>();
        this.requeueStaleInflightScript.setResultType(Long.class);

        // Load Lua scripts from classpath for maintainability.
        try {
            ClassPathResource res = new ClassPathResource("redis/move_max_to_inflight.lua");
            byte[] bytes = res.getInputStream().readAllBytes();
            this.moveMaxToInflightScript.setScriptText(new String(bytes, StandardCharsets.UTF_8));

            ClassPathResource ack = new ClassPathResource("redis/ack_and_requeue.lua");
            byte[] ackBytes = ack.getInputStream().readAllBytes();
            this.ackAndRequeueScript.setScriptText(new String(ackBytes, StandardCharsets.UTF_8));

            ClassPathResource requeue = new ClassPathResource("redis/requeue_stale_inflight.lua");
            byte[] requeueBytes = requeue.getInputStream().readAllBytes();
            this.requeueStaleInflightScript.setScriptText(new String(requeueBytes, StandardCharsets.UTF_8));
        } catch (Exception e) {
            throw new IllegalStateException("Failed to load Redis Lua scripts from classpath", e);
        }
    }

    /**
     * Atomically: ZPOPMAX from tasks -> ZADD inflight {keyword} {nowTs}.
     * Returns keyword or null if nothing.
     */
    public String fetchNextKeywordBlockingCompatible() {
        // Note: Lua is non-blocking; the Termux feeder does the blocking with BZPOPMAX+ZADD.
        // Backend does not use this method in production hot path.
        List<Object> res = redis.execute(
                moveMaxToInflightScript,
                List.of(TASKS_ZSET, INFLIGHT_ZSET, LAST_SCORE_HASH),
                String.valueOf(Instant.now().getEpochSecond())
        );
        if (res == null || res.isEmpty()) {
            return null;
        }
        // Script returns: {member, oldScore}
        return Objects.toString(res.get(0), null);
    }

    /**
     * ACK a keyword (remove from inflight), and requeue it back to tasks using last_score + delta.
     *
     * Returns 1 if successful.
     */
    public long ackAndRequeue(String keyword, double delta) {
        if (keyword == null || keyword.isBlank()) {
            return 0;
        }
        Long res = redis.execute(
                ackAndRequeueScript,
                List.of(INFLIGHT_ZSET, TASKS_ZSET, LAST_SCORE_HASH),
                keyword,
                String.valueOf(delta)
        );
        return res == null ? 0 : res;
    }

    /**
     * Move stale inflight keywords (timestamp <= cutoffTs) back to tasks with a penalty.
     * Returns moved count.
     */
    public long requeueStaleInflight(long cutoffTs, double penaltyDelta, int limit) {
        Long res = redis.execute(
                requeueStaleInflightScript,
                List.of(INFLIGHT_ZSET, TASKS_ZSET, LAST_SCORE_HASH),
                String.valueOf(cutoffTs),
                String.valueOf(penaltyDelta),
                String.valueOf(limit)
        );
        return res == null ? 0 : res;
    }

    /**
     * Spawn a new entity as a keyword task, deduped via KNOWN_SET.
     */
    public boolean spawnEntityIfNew(String entity, double initialScore) {
        if (entity == null) {
            return false;
        }
        String normalized = entity.trim();
        if (normalized.isBlank()) {
            return false;
        }

        Long added = redis.opsForSet().add(KNOWN_SET, normalized);
        if (added != null && added > 0) {
            redis.opsForZSet().add(TASKS_ZSET, normalized, initialScore);
            return true;
        }
        return false;
    }
}
