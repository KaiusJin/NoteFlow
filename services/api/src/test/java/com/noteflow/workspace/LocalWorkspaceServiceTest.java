package com.noteflow.workspace;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;

class LocalWorkspaceServiceTest {
    private static final UUID LOCAL_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void fallsBackToLocalWorkspaceWithoutAuthentication() {
        assertEquals(LOCAL_ID, new LocalWorkspaceService(LOCAL_ID).currentWorkspaceId());
    }

    @Test
    void usesVerifiedJwtSubjectForCloudRequest() {
        UUID subject = UUID.randomUUID();
        Jwt token = new Jwt(
            "token",
            Instant.now(),
            Instant.now().plusSeconds(60),
            Map.of("alg", "none"),
            Map.of("sub", subject.toString())
        );
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(
            token, java.util.List.of(new SimpleGrantedAuthority("ROLE_AUTHENTICATED"))));

        assertEquals(subject, new LocalWorkspaceService(LOCAL_ID).currentWorkspaceId());
    }

    @Test
    void rejectsNonUuidJwtSubject() {
        Jwt token = new Jwt(
            "token",
            Instant.now(),
            Instant.now().plusSeconds(60),
            Map.of("alg", "none"),
            Map.of("sub", "not-a-uuid")
        );
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(
            token, java.util.List.of(new SimpleGrantedAuthority("ROLE_AUTHENTICATED"))));

        assertThrows(AccessDeniedException.class,
            () -> new LocalWorkspaceService(LOCAL_ID).currentWorkspaceId());
    }
}
