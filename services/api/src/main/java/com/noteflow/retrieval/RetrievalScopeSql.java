package com.noteflow.retrieval;

import java.util.ArrayList;
import java.util.List;

final class RetrievalScopeSql {
    private RetrievalScopeSql() {
    }

    static String clause(RetrievalScope scope, List<Object> params) {
        List<String> domainClauses = new ArrayList<>();
        if (!scope.pdfDocumentIds().isEmpty()) {
            domainClauses.add(
                "embeddings.source_domain = 'PDF' AND embeddings.document_id IN ("
                    + placeholders(scope.pdfDocumentIds().size()) + ")"
            );
            params.addAll(scope.pdfDocumentIds());
        }
        if (!scope.aiNoteDocumentIds().isEmpty()) {
            domainClauses.add(
                """
                embeddings.source_domain = 'AI_NOTE'
                AND embeddings.document_id IN (%s)
                AND notes.status = 'READY'
                AND notes.note_version = (
                  SELECT MAX(latest.note_version)
                  FROM document_ai_notes latest
                  WHERE latest.document_id = embeddings.document_id
                    AND latest.status = 'READY'
                )
                """.formatted(placeholders(scope.aiNoteDocumentIds().size())).trim()
            );
            params.addAll(scope.aiNoteDocumentIds());
        }
        return "(" + String.join(" OR ", domainClauses) + ")";
    }

    private static String placeholders(int count) {
        return String.join(",", java.util.Collections.nCopies(count, "?"));
    }
}
