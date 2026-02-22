package com.hardwarepulse.backend.worker;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import com.hardwarepulse.backend.service.pulse.SpiderSchedulerService;

@Component
public class SpiderSeedRunner implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(SpiderSeedRunner.class);

    private static final String[] SEEDS = new String[] {
            "E5-2673 v3",
            "X99 主板",
            "DDR4 ECC 32G",
            "CX341A",
            "N100 准系统",
    };

    private final StringRedisTemplate redis;

    public SpiderSeedRunner(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public void run(String... args) {
        long added = 0;
        for (String seed : SEEDS) {
            if (seed == null || seed.isBlank()) {
                continue;
            }

            // Canonical known vocabulary.
            redis.opsForSet().add(SpiderSchedulerService.KNOWN_SET, seed);

            // Seed tasks with high initial priority.
            Boolean ok = redis.opsForZSet().add(SpiderSchedulerService.TASKS_ZSET, seed, 100.0);
            if (Boolean.TRUE.equals(ok)) {
                added++;
            }
            redis.opsForHash().put(SpiderSchedulerService.LAST_SCORE_HASH, seed, "100");
        }

        log.info("Spider seeds ensured. total={} newlyAddedZset={}", SEEDS.length, added);
    }
}
