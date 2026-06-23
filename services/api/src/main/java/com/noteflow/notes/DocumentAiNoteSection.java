package com.noteflow.notes;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_ai_note_sections", uniqueConstraints = {
    @UniqueConstraint(name = "uq_document_ai_note_sections_note_index", columnNames = {"noteId", "sectionIndex"})
})
public class DocumentAiNoteSection {
    @Id
    private UUID id;

    private UUID noteId;
    private UUID documentId;
    private int sectionIndex;
    private String sectionType;
    private String heading;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    private Integer pageStart;
    private Integer pageEnd;
    @Column(columnDefinition = "TEXT")
    private String sourceChunkIdsJson;
    @Column(columnDefinition = "TEXT")
    private String sourcePagesJson;
    private Double confidence;
    @Column(columnDefinition = "TEXT")
    private String warningsJson;
    @Column(columnDefinition = "TEXT")
    private String metadataJson;
    private Instant createdAt;

    protected DocumentAiNoteSection() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getNoteId() {
        return noteId;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public int getSectionIndex() {
        return sectionIndex;
    }

    public String getSectionType() {
        return sectionType;
    }

    public String getHeading() {
        return heading;
    }

    public String getMarkdown() {
        return markdown;
    }

    public Integer getPageStart() {
        return pageStart;
    }

    public Integer getPageEnd() {
        return pageEnd;
    }

    public String getSourceChunkIdsJson() {
        return sourceChunkIdsJson;
    }

    public String getSourcePagesJson() {
        return sourcePagesJson;
    }

    public Double getConfidence() {
        return confidence;
    }

    public String getWarningsJson() {
        return warningsJson;
    }

    public String getMetadataJson() {
        return metadataJson;
    }
}
