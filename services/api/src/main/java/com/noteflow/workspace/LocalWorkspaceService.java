package com.noteflow.workspace;

import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/** Stable namespace for this local installation; not an authenticated user. */
@Service
public class LocalWorkspaceService {
    private final UUID workspaceId;

    public LocalWorkspaceService(@Value("${noteflow.local.workspace-id}") UUID workspaceId) {
        this.workspaceId = workspaceId;
    }

    public UUID currentWorkspaceId() { return workspaceId; }

    /** Compatibility name while legacy database columns are still user_id. */
    public UUID currentUserId() { return workspaceId; }
}
