package com.noteflow.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CloudRunJobLauncherTest {
    private static final String RESOURCE = "projects/noteflow-demo/locations/us-central1/jobs/noteflow-worker";

    private HttpServer server;
    private String baseUrl;
    private final AtomicInteger tokenRequests = new AtomicInteger();
    private final AtomicInteger launchRequests = new AtomicInteger();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/token", exchange -> {
            tokenRequests.incrementAndGet();
            assertEquals("Google", exchange.getRequestHeaders().getFirst("Metadata-Flavor"));
            respond(exchange, 200, "{\"access_token\":\"test-token\",\"expires_in\":3600}");
        });
        server.createContext("/v2/" + RESOURCE + ":run", exchange -> {
            launchRequests.incrementAndGet();
            assertEquals("Bearer test-token", exchange.getRequestHeaders().getFirst("Authorization"));
            assertEquals("POST", exchange.getRequestMethod());
            respond(exchange, 200, "{}");
        });
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    @Test
    void launchesJobWithMetadataIdentityAndCachesToken() {
        CloudRunJobLauncher launcher = launcher(RESOURCE);

        launcher.launch();
        launcher.launch();

        assertEquals(1, tokenRequests.get());
        assertEquals(2, launchRequests.get());
    }

    @Test
    void rejectsMalformedJobResourceAtStartup() {
        assertThrows(IllegalArgumentException.class, () -> launcher("../jobs/attacker"));
    }

    private CloudRunJobLauncher launcher(String resource) {
        return new CloudRunJobLauncher(
            HttpClient.newHttpClient(),
            new ObjectMapper(),
            resource,
            baseUrl + "/token",
            baseUrl + "/v2",
            5
        );
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }
}
