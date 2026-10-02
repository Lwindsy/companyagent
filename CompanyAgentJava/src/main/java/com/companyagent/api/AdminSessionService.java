package com.companyagent.api;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
public class AdminSessionService {

    private static final Duration SESSION_TTL = Duration.ofHours(8);
    private final SecureRandom random = new SecureRandom();
    private final Map<String, Instant> sessions = new ConcurrentHashMap<>();

    public LoginResult login(String username, String password) {
        if (adminPassword().isEmpty()) {
            // No built-in default: administrator sign-in stays disabled until ADMIN_PASSWORD is set.
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED,
                    "Administrator sign-in is disabled: ADMIN_PASSWORD is not configured");
        }
        if (!credentialsMatch(username, password)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid administrator credentials");
        }
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        sessions.put(token, Instant.now().plus(SESSION_TTL));
        return new LoginResult(token, SESSION_TTL.toSeconds(), adminUsername());
    }

    public void requireSession(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Administrator authentication is required");
        }
        String token = authorization.substring("Bearer ".length()).trim();
        Instant expiresAt = sessions.get(token);
        if (expiresAt == null || !Instant.now().isBefore(expiresAt)) {
            sessions.remove(token);
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Administrator session has expired");
        }
    }

    private boolean credentialsMatch(String username, String password) {
        return MessageDigest.isEqual(safe(username).getBytes(StandardCharsets.UTF_8), adminUsername().getBytes(StandardCharsets.UTF_8))
                && MessageDigest.isEqual(safe(password).getBytes(StandardCharsets.UTF_8), adminPassword().getBytes(StandardCharsets.UTF_8));
    }

    private String adminUsername() {
        return System.getenv().getOrDefault("ADMIN_USERNAME", "admin");
    }

    private String adminPassword() {
        return System.getenv().getOrDefault("ADMIN_PASSWORD", "");
    }

    private String safe(String value) {
        return value == null ? "" : value;
    }

    public record LoginResult(String accessToken, long expiresIn, String username) {
    }
}
