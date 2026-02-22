package com.hardwarepulse.backend.service.pulse;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class HardwareEntityExtractor {

    // Static final patterns to prevent repetitive compilation in hot paths.
    private static final Pattern CPU = Pattern.compile(
            "(?:E5|i\\d|R\\d)-[A-Za-z0-9-]+(?:\\s*v\\d)?",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern MB = Pattern.compile(
            "(?:X99|B660|Z790)[A-Za-z-]*",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern RAM = Pattern.compile(
            "(?:DDR\\d)\\s*(?:ECC|RECC)?\\s*\\d{1,3}G",
            Pattern.CASE_INSENSITIVE
    );

    private static final List<Pattern> PATTERNS = List.of(CPU, MB, RAM);

    private HardwareEntityExtractor() {
    }

    public static Set<String> extractFromTitles(List<String> titles) {
        if (titles == null || titles.isEmpty()) {
            return Set.of();
        }
        LinkedHashSet<String> out = new LinkedHashSet<>();
        for (String t : titles) {
            if (t == null || t.isBlank()) {
                continue;
            }
            for (Pattern p : PATTERNS) {
                Matcher m = p.matcher(t);
                while (m.find()) {
                    String hit = m.group();
                    if (hit != null && !hit.isBlank()) {
                        out.add(hit.trim());
                    }
                }
            }
        }
        return out;
    }

    public static List<String> extractToList(List<String> titles) {
        return new ArrayList<>(extractFromTitles(titles));
    }
}
