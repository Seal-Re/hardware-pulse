package com.hardwarepulse.backend.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.hardwarepulse.backend.model.dto.pulse.PulseRawBatchDTO;
import com.hardwarepulse.backend.service.pulse.PulseIngestService;

@RestController
@RequestMapping("/api/pulse")
public class PulseRawController {

    private static final Logger log = LoggerFactory.getLogger(PulseRawController.class);

    private final PulseIngestService ingestService;

    public PulseRawController(PulseIngestService ingestService) {
        this.ingestService = ingestService;
    }

    @PostMapping("/raw")
    public ResponseEntity<Void> ingestRaw(@RequestBody PulseRawBatchDTO batch) {
        if (batch == null) {
            // Provide a predictable 400 with a human-readable error instead of a generic Spring message.
            return ResponseEntity.badRequest().build();
        }

        // Log minimal fields to aid debugging of upstream payload mismatch.
        int n = batch.items() == null ? 0 : batch.items().size();
        log.info("/api/pulse/raw accept keyword='{}' platform='{}' items={}", batch.keyword(), batch.platform(), n);
        ingestService.ingest(batch);
        return ResponseEntity.accepted().build();
    }
}
