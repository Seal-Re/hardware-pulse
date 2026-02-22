package com.hardwarepulse.backend.repository;

import com.hardwarepulse.backend.model.entity.StandardSku;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface StandardSkuRepository extends JpaRepository<StandardSku, Long> {
    Optional<StandardSku> findByBrandAndModelName(String brand, String modelName);
}
