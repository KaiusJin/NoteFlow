package com.noteflow.security;

import com.noteflow.workspace.InternalWorkspacePrincipal;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.UUID;
import org.springframework.context.annotation.Profile;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Profile("cloud")
public class InternalServiceAuthenticationFilter extends OncePerRequestFilter {
    public static final String TOKEN_HEADER = "X-NoteFlow-Internal-Token";
    public static final String WORKSPACE_HEADER = "X-NoteFlow-Workspace-Id";

    private final byte[] expectedToken;

    public InternalServiceAuthenticationFilter(
            @org.springframework.beans.factory.annotation.Value("${noteflow.security.internal-token}") String expectedToken) {
        String normalized = expectedToken.trim();
        if (normalized.length() < 32) {
            throw new IllegalStateException("NOTEFLOW_INTERNAL_TOKEN must contain at least 32 characters");
        }
        this.expectedToken = normalized.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith("/internal/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String suppliedToken = request.getHeader(TOKEN_HEADER);
        if (suppliedToken == null || !MessageDigest.isEqual(
                expectedToken, suppliedToken.getBytes(StandardCharsets.UTF_8))) {
            unauthorized(response, "Invalid internal service credentials");
            return;
        }

        UUID workspaceId;
        try {
            workspaceId = UUID.fromString(request.getHeader(WORKSPACE_HEADER));
        } catch (RuntimeException invalidWorkspace) {
            unauthorized(response, "Missing or invalid internal workspace identity");
            return;
        }

        InternalWorkspacePrincipal principal = new InternalWorkspacePrincipal("noteflow-worker", workspaceId);
        UsernamePasswordAuthenticationToken authentication = UsernamePasswordAuthenticationToken.authenticated(
            principal,
            null,
            List.of(new SimpleGrantedAuthority("ROLE_INTERNAL_SERVICE"))
        );
        SecurityContextHolder.getContext().setAuthentication(authentication);
        chain.doFilter(request, response);
    }

    private void unauthorized(HttpServletResponse response, String message) throws IOException {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write("{\"message\":\"" + message + "\"}");
    }
}
