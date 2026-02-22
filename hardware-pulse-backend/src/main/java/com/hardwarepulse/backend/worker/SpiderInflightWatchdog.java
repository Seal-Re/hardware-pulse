package com.hardwarepulse.backend.worker;

import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import com.hardwarepulse.backend.service.pulse.SpiderSchedulerService;

@Component
public class SpiderInflightWatchdog {

    private static final Logger log = LoggerFactory.getLogger(SpiderInflightWatchdog.class);

    private final SpiderSchedulerService scheduler;

    public SpiderInflightWatchdog(SpiderSchedulerService scheduler) {
        this.scheduler = scheduler;
    }

    /**
     * Every 5 minutes: requeue inflight keywords older than 10 minutes with a small penalty.
     */
    @Scheduled(fixedDelay = 5 * 60 * 1000L)
    public void requeueStaleInflight() {
        long now = Instant.now().getEpochSecond();
        long cutoff = now - (10 * 60);

        long moved = scheduler.requeueStaleInflight(cutoff, -2.0, 200);
        if (moved > 0) {
            log.warn("Requeued stale inflight keywords. moved={} cutoffTs={}", moved, cutoff);
        }
    }
}
