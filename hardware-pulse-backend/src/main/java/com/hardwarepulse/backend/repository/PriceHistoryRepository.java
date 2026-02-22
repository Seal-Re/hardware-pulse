package com.hardwarepulse.backend.repository;

import com.hardwarepulse.backend.model.entity.PriceHistory;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PriceHistoryRepository extends JpaRepository<PriceHistory, com.hardwarepulse.backend.model.entity.PriceHistoryId> {
}
