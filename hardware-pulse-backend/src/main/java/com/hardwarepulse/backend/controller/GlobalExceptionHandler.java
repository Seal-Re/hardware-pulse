package com.hardwarepulse.backend.controller;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import com.hardwarepulse.backend.service.pulse.PulseIngestService;

/**
 * Surface JSON binding problems as explicit 400 logs.
 *
 * When the crawler payload drifts (field name/type mismatch), Spring may return a generic 400.
 * Logging the root exception message here makes it trivial to pinpoint the offending field.
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<String> handleNotReadable(HttpMessageNotReadableException e) {
        // Usually thrown by Jackson when request JSON cannot be parsed/bound to DTO.
        String msg = e.getMostSpecificCause() == null ? e.getMessage() : e.getMostSpecificCause().getMessage();
        log.warn("Bad request JSON: {}", msg);
        return ResponseEntity.badRequest().body("Bad request JSON: " + msg);
    }

    @ExceptionHandler(PulseIngestService.BackpressureException.class)
    public ResponseEntity<String> handleBackpressure(PulseIngestService.BackpressureException e) {
        // Signal the crawler to slow down / backoff.
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body("Backpressure: " + e.getMessage());
    }
}
