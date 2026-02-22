package com.hardwarepulse.backend.repository;

import com.hardwarepulse.backend.model.entity.RawListing;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface RawListingRepository extends JpaRepository<RawListing, Long> {
    Optional<RawListing> findByExternalId(String externalId);
}
