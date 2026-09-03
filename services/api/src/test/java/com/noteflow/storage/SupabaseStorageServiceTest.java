package com.noteflow.storage;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

class SupabaseStorageServiceTest {
    private static final byte[] PNG = {
        (byte) 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x01
    };
    private static final UUID USER_ID = UUID.fromString("00000000-0000-0000-0000-000000000021");
    private static final UUID DOCUMENT_ID = UUID.fromString("00000000-0000-0000-0000-000000000022");

    private HttpServer server;
    private String baseUrl;
    private final AtomicInteger uploadRequests = new AtomicInteger();
    private final AtomicInteger deleteRequests = new AtomicInteger();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/storage/v1/object/noteflow-private/users/", this::handleObjectRequest);
        server.createContext("/storage/v1/object/noteflow-private", exchange -> {
            deleteRequests.incrementAndGet();
            assertEquals("DELETE", exchange.getRequestMethod());
            assertEquals("sb_secret_test", exchange.getRequestHeaders().getFirst("apikey"));
            assertNull(exchange.getRequestHeaders().getFirst("Authorization"));
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            assertEquals(
                "{\"prefixes\":[\"users/00000000-0000-0000-0000-000000000021/documents/00000000-0000-0000-0000-000000000022/source.pdf\"]}",
                body
            );
            respond(exchange, 200, "[]".getBytes(StandardCharsets.UTF_8), "application/json");
        });
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    @Test
    void uploadsAndDeletesPrivatePdfWithServerSecret() {
        SupabaseStorageService storage = storage();
        byte[] pdf = "%PDF-1.7 test".getBytes(StandardCharsets.US_ASCII);

        StoredFile stored = storage.savePdf(
            USER_ID,
            DOCUMENT_ID,
            new MockMultipartFile("file", "lecture.pdf", "application/pdf", pdf)
        );
        storage.deleteIfExists(stored.storagePath());

        assertEquals(
            "supabase://noteflow-private/users/" + USER_ID + "/documents/" + DOCUMENT_ID + "/source.pdf",
            stored.storagePath()
        );
        assertEquals(1, uploadRequests.get());
        assertEquals(1, deleteRequests.get());
    }

    @Test
    void downloadsAndValidatesPrivatePng() {
        StoredObject object = storage().readPng(
            "supabase://noteflow-private/users/" + USER_ID + "/documents/" + DOCUMENT_ID + "/rendered/page-001.png"
        );

        assertArrayEquals(PNG, object.content());
        assertEquals("image/png", object.contentType());
    }

    @Test
    void refusesReferencesOutsideConfiguredBucket() {
        assertThrows(
            IllegalArgumentException.class,
            () -> storage().readPng("supabase://another-bucket/users/victim/page.png")
        );
    }

    private SupabaseStorageService storage() {
        return new SupabaseStorageService(
            HttpClient.newHttpClient(),
            new ObjectMapper(),
            baseUrl,
            "sb_secret_test",
            "noteflow-private",
            5
        );
    }

    private void handleObjectRequest(HttpExchange exchange) throws IOException {
        assertEquals("sb_secret_test", exchange.getRequestHeaders().getFirst("apikey"));
        assertNull(exchange.getRequestHeaders().getFirst("Authorization"));
        if (exchange.getRequestURI().getPath().endsWith("source.pdf")) {
            uploadRequests.incrementAndGet();
            assertEquals("POST", exchange.getRequestMethod());
            assertEquals("true", exchange.getRequestHeaders().getFirst("x-upsert"));
            respond(exchange, 200, "{}".getBytes(StandardCharsets.UTF_8), "application/json");
            return;
        }
        assertEquals("GET", exchange.getRequestMethod());
        respond(exchange, 200, PNG, "image/png");
    }

    private static void respond(HttpExchange exchange, int status, byte[] body, String contentType) throws IOException {
        exchange.getResponseHeaders().set("Content-Type", contentType);
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }
}
