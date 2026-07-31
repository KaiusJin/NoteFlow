package com.noteflow.library;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

class NoteImportReaderTest {

    @Test
    void readsSupportedUtf8NoteWithinLimit() {
        NoteImportReader reader = new NoteImportReader(32);
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "lesson.md",
                "text/markdown; charset=utf-8",
                "# Résumé".getBytes(UTF_8)
        );

        assertEquals("# Résumé", reader.readUtf8(file));
    }

    @Test
    void rejectsUnsupportedExtension() {
        NoteImportReader reader = new NoteImportReader(32);
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "lesson.html",
                "text/plain",
                "text".getBytes(UTF_8)
        );

        assertThrows(IllegalArgumentException.class, () -> reader.readUtf8(file));
    }

    @Test
    void rejectsBodyLargerThanConfiguredLimit() {
        NoteImportReader reader = new NoteImportReader(4);
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "lesson.txt",
                "text/plain",
                "12345".getBytes(UTF_8)
        );

        assertThrows(IllegalArgumentException.class, () -> reader.readUtf8(file));
    }

    @Test
    void rejectsMalformedUtf8() {
        NoteImportReader reader = new NoteImportReader(32);
        MockMultipartFile file = new MockMultipartFile(
                "file",
                "lesson.txt",
                "text/plain",
                new byte[] {(byte) 0xc3, 0x28}
        );

        assertThrows(IllegalArgumentException.class, () -> reader.readUtf8(file));
    }
}
