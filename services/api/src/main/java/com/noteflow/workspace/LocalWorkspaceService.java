package com.noteflow.workspace;

import java.util.UUID;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Resolves the tenant identity for the current execution.
 *
 * <p>The compatibility class name is retained while legacy columns are still
 * named {@code user_id}. In cloud requests the verified JWT subject is the
 * personal-workspace id. Local development continues to use the configured
 * installation id. Trusted worker calls carry an explicitly authenticated
 * workspace principal.</p>
 */
@Service
public class LocalWorkspaceService {
    private final UUID workspaceId;

    public LocalWorkspaceService(@Value("${noteflow.local.workspace-id}") UUID workspaceId) {
        this.workspaceId = workspaceId;
    }

    public UUID currentWorkspaceId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication instanceof JwtAuthenticationToken jwt && authentication.isAuthenticated()) {
            return parseAuthenticatedId(jwt.getToken().getSubject());
        }
        if (authentication != null
                && authentication.isAuthenticated()
                && authentication.getPrincipal() instanceof InternalWorkspacePrincipal internal) {
            return internal.workspaceId();
        }
        return workspaceId;
    }

    /** Compatibility name while legacy database columns are still user_id. */
    public UUID currentUserId() { return currentWorkspaceId(); }

    private UUID parseAuthenticatedId(String subject) {
        try {
            return UUID.fromString(subject);
        } catch (RuntimeException invalidSubject) {
            throw new AccessDeniedException("Authenticated subject is not a valid workspace identity", invalidSubject);
        }
    }
}
