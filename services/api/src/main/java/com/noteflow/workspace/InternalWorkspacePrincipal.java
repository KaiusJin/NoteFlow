package com.noteflow.workspace;

import java.util.UUID;

/** Tenant identity asserted by an authenticated internal worker request. */
public record InternalWorkspacePrincipal(String serviceName, UUID workspaceId) {}
