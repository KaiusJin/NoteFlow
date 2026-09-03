package com.noteflow.documents;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

class DocumentServiceTest {

    @Test
    void recognizesDatabaseNamesForActiveEmbeddingTasks() {
        assertTrue(DocumentService.isActiveEmbeddingStatus("PENDING"));
        assertTrue(DocumentService.isActiveEmbeddingStatus("PROCESSING"));
        assertTrue(DocumentService.isActiveEmbeddingStatus("RETRYING"));
        assertFalse(DocumentService.isActiveEmbeddingStatus("FAILED"));
    }

    @Test
    void rejectsPdfExtensionWithoutPdfSignature() {
        DocumentService service = new DocumentService(null, null, null, null, null, null);
        MockMultipartFile fakePdf = new MockMultipartFile(
                "file",
                "fake.pdf",
                "application/pdf",
                "<html>not a PDF</html>".getBytes(UTF_8)
        );

        assertThrows(
                IllegalArgumentException.class,
                () -> service.upload(fakePdf, DocumentType.OTHER, null)
        );
    }
}
