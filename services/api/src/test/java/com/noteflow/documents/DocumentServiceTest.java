package com.noteflow.documents;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

class DocumentServiceTest {

    @Test
    void rejectsPdfExtensionWithoutPdfSignature() {
        DocumentService service = new DocumentService(null, null, null, null, null, null, null);
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
