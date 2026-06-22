package com.noteflow.documents;

import java.util.UUID;

public record CreateDocumentResponse(UUID documentId, UUID taskId, DocumentStatus status) {
}
