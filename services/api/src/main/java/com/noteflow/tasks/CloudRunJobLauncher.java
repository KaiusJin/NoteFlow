package com.noteflow.tasks;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class CloudRunJobLauncher implements WorkerJobLauncher {
    private static final Pattern JOB_RESOURCE = Pattern.compile(
        "^projects/[a-z0-9][a-z0-9-]{3,61}[a-z0-9]/locations/[a-z0-9-]+/jobs/[a-z][a-z0-9-]{0,62}$"
    );
    private static final Duration TOKEN_REFRESH_MARGIN = Duration.ofSeconds(60);

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String jobResource;
    private final URI metadataTokenUri;
    private final URI runJobUri;
    private final Duration requestTimeout;
    private final String coordinationKey;
    private volatile CachedToken cachedToken;

    public CloudRunJobLauncher(
            HttpClient externalHttpClient,
            ObjectMapper objectMapper,
            @Value("${noteflow.worker.cloud-run-job-resource:}") String jobResource,
            @Value("${noteflow.worker.metadata-token-url:http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token}") String metadataTokenUrl,
            @Value("${noteflow.worker.cloud-run-api-base-url:https://run.googleapis.com/v2}") String cloudRunApiBaseUrl,
            @Value("${noteflow.worker.launch-timeout-seconds:10}") int requestTimeoutSeconds) {
        this.httpClient = externalHttpClient;
        this.objectMapper = objectMapper;
        this.jobResource = jobResource == null ? "" : jobResource.trim();
        if (!this.jobResource.isEmpty() && !JOB_RESOURCE.matcher(this.jobResource).matches()) {
            throw new IllegalArgumentException(
                "noteflow.worker.cloud-run-job-resource must be a full Cloud Run job resource name"
            );
        }
        this.metadataTokenUri = URI.create(metadataTokenUrl);
        String apiBase = cloudRunApiBaseUrl.replaceAll("/+$", "");
        this.runJobUri = this.jobResource.isEmpty()
            ? URI.create(apiBase)
            : URI.create(apiBase + "/" + this.jobResource + ":run");
        this.requestTimeout = Duration.ofSeconds(Math.max(2, requestTimeoutSeconds));
        this.coordinationKey = "noteflow:worker:wakeup:" + sha256(this.jobResource);
    }

    @Override
    public boolean configured() {
        return !jobResource.isEmpty();
    }

    @Override
    public String coordinationKey() {
        return coordinationKey;
    }

    @Override
    public void launch() {
        if (!configured()) {
            return;
        }
        HttpRequest request = HttpRequest.newBuilder(runJobUri)
            .timeout(requestTimeout)
            .header("Authorization", "Bearer " + accessToken())
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString("{}"))
            .build();
        HttpResponse<String> response = send(request);
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException("Cloud Run Jobs API returned HTTP " + response.statusCode());
        }
    }

    private synchronized String accessToken() {
        Instant now = Instant.now();
        if (cachedToken != null && now.isBefore(cachedToken.refreshAfter())) {
            return cachedToken.value();
        }
        HttpRequest request = HttpRequest.newBuilder(metadataTokenUri)
            .timeout(requestTimeout)
            .header("Metadata-Flavor", "Google")
            .GET()
            .build();
        HttpResponse<String> response = send(request);
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException("Google metadata token endpoint returned HTTP " + response.statusCode());
        }
        try {
            JsonNode json = objectMapper.readTree(response.body());
            String value = json.path("access_token").asText("");
            long expiresIn = json.path("expires_in").asLong(0);
            if (value.isBlank() || expiresIn <= 0) {
                throw new IllegalStateException("Google metadata token response is incomplete");
            }
            Duration cacheDuration = Duration.ofSeconds(expiresIn).minus(TOKEN_REFRESH_MARGIN);
            if (cacheDuration.isNegative() || cacheDuration.isZero()) {
                cacheDuration = Duration.ofSeconds(1);
            }
            cachedToken = new CachedToken(value, now.plus(cacheDuration));
            return value;
        } catch (IOException error) {
            throw new IllegalStateException("Could not parse Google metadata token response", error);
        }
    }

    private HttpResponse<String> send(HttpRequest request) {
        try {
            return httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException error) {
            throw new IllegalStateException("Cloud Run control-plane request failed", error);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Cloud Run control-plane request was interrupted", error);
        }
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))
            );
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private record CachedToken(String value, Instant refreshAfter) {
    }
}
