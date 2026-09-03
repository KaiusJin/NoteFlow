package com.noteflow.storage;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

@Service
@Profile("cloud")
public class SupabaseStorageService implements DocumentObjectStorage, PngObjectStorage {
    private static final byte[] PNG_SIGNATURE = {
        (byte) 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a
    };
    private static final Pattern BUCKET_NAME = Pattern.compile("^[a-z0-9][a-z0-9._-]{0,62}$");
    private static final int MAX_PNG_BYTES = 25 * 1024 * 1024;

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final URI storageApiBase;
    private final String secretKey;
    private final String bucket;
    private final Duration requestTimeout;

    public SupabaseStorageService(
            HttpClient externalHttpClient,
            ObjectMapper objectMapper,
            @Value("${noteflow.storage.supabase-url}") String supabaseUrl,
            @Value("${noteflow.storage.supabase-secret-key}") String secretKey,
            @Value("${noteflow.storage.supabase-bucket:noteflow-private}") String bucket,
            @Value("${noteflow.storage.request-timeout-seconds:30}") int requestTimeoutSeconds) {
        this.httpClient = externalHttpClient;
        this.objectMapper = objectMapper;
        this.storageApiBase = URI.create(supabaseUrl.replaceAll("/+$", "") + "/storage/v1/");
        this.secretKey = secretKey == null ? "" : secretKey.trim();
        this.bucket = bucket == null ? "" : bucket.trim();
        this.requestTimeout = Duration.ofSeconds(Math.max(5, requestTimeoutSeconds));
        if (this.secretKey.isBlank()) {
            throw new IllegalArgumentException("Supabase secret key is required in the cloud profile");
        }
        if (!BUCKET_NAME.matcher(this.bucket).matches()) {
            throw new IllegalArgumentException("Supabase storage bucket name is invalid");
        }
    }

    @Override
    public StoredFile savePdf(UUID userId, UUID documentId, MultipartFile file) {
        String objectPath = "users/" + userId + "/documents/" + documentId + "/source.pdf";
        byte[] content;
        try {
            content = file.getBytes();
        } catch (IOException error) {
            throw new IllegalStateException("Could not read uploaded PDF", error);
        }
        String reference = objectReference(bucket, objectPath);
        try {
            HttpRequest request = requestBuilder(objectUri(bucket, objectPath))
                .header("Content-Type", "application/pdf")
                .header("cache-control", "max-age=3600")
                .header("x-upsert", "true")
                .POST(HttpRequest.BodyPublishers.ofByteArray(content))
                .build();
            requireSuccess(send(request, HttpResponse.BodyHandlers.ofByteArray()), "upload PDF");
            return new StoredFile(reference, "application/pdf", content.length);
        } catch (RuntimeException uploadError) {
            try {
                deleteIfExists(reference);
            } catch (RuntimeException cleanupError) {
                uploadError.addSuppressed(cleanupError);
            }
            throw uploadError;
        }
    }

    @Override
    public void deleteIfExists(String storagePath) {
        ObjectReference reference = parseReference(storagePath);
        byte[] body;
        try {
            body = objectMapper.writeValueAsBytes(Map.of("prefixes", List.of(reference.path())));
        } catch (JsonProcessingException error) {
            throw new IllegalStateException("Could not encode Supabase delete request", error);
        }
        HttpRequest request = requestBuilder(storageApiBase.resolve("object/" + encodeSegment(bucket)))
            .header("Content-Type", "application/json")
            .method("DELETE", HttpRequest.BodyPublishers.ofByteArray(body))
            .build();
        HttpResponse<byte[]> response = send(request, HttpResponse.BodyHandlers.ofByteArray());
        if (response.statusCode() != 404) {
            requireSuccess(response, "delete object");
        }
    }

    @Override
    public StoredObject readPng(String storagePath) {
        ObjectReference reference = parseReference(storagePath);
        if (!reference.path().toLowerCase(java.util.Locale.ROOT).endsWith(".png")) {
            throw new IllegalArgumentException("Asset is not a PNG object");
        }
        HttpRequest request = requestBuilder(objectUri(reference.bucket(), reference.path())).GET().build();
        HttpResponse<InputStream> response = send(request, HttpResponse.BodyHandlers.ofInputStream());
        requireSuccess(response, "download PNG");
        byte[] content;
        try (InputStream input = response.body()) {
            content = input.readNBytes(MAX_PNG_BYTES + 1);
        } catch (IOException error) {
            throw new IllegalStateException("Could not read PNG from Supabase Storage", error);
        }
        if (content.length > MAX_PNG_BYTES || content.length < PNG_SIGNATURE.length
                || !Arrays.equals(PNG_SIGNATURE, Arrays.copyOf(content, PNG_SIGNATURE.length))) {
            throw new IllegalArgumentException("Stored asset is not a valid PNG file");
        }
        return new StoredObject(content, "image/png");
    }

    private HttpRequest.Builder requestBuilder(URI uri) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(uri)
            .timeout(requestTimeout)
            .header("apikey", secretKey);
        // Legacy service_role keys are JWTs and are also accepted as bearer
        // credentials. New sb_secret keys must only use the apikey header.
        if (secretKey.startsWith("eyJ")) {
            builder.header("Authorization", "Bearer " + secretKey);
        }
        return builder;
    }

    private URI objectUri(String objectBucket, String path) {
        String encodedPath = Arrays.stream(path.split("/"))
            .map(SupabaseStorageService::encodeSegment)
            .collect(java.util.stream.Collectors.joining("/"));
        return storageApiBase.resolve("object/" + encodeSegment(objectBucket) + "/" + encodedPath);
    }

    private ObjectReference parseReference(String storagePath) {
        if (storagePath == null || storagePath.isBlank()) {
            throw new IllegalArgumentException("Storage object path is missing");
        }
        URI reference = URI.create(storagePath);
        String referenceBucket = reference.getHost();
        String path = reference.getPath() == null ? "" : reference.getPath().replaceFirst("^/", "");
        if (!"supabase".equals(reference.getScheme()) || !bucket.equals(referenceBucket)
                || path.isBlank() || path.contains("..") || path.contains("//")) {
            throw new IllegalArgumentException("Storage object path is outside the configured private bucket");
        }
        return new ObjectReference(referenceBucket, path);
    }

    private static String objectReference(String objectBucket, String path) {
        return "supabase://" + objectBucket + "/" + path;
    }

    private static String encodeSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private <T> HttpResponse<T> send(HttpRequest request, HttpResponse.BodyHandler<T> handler) {
        try {
            return httpClient.send(request, handler);
        } catch (IOException error) {
            throw new IllegalStateException("Supabase Storage request failed", error);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Supabase Storage request was interrupted", error);
        }
    }

    private void requireSuccess(HttpResponse<?> response, String operation) {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException(
                "Could not " + operation + " in Supabase Storage (HTTP " + response.statusCode() + ")"
            );
        }
    }

    private record ObjectReference(String bucket, String path) {
    }
}
