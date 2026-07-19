package com.noteflow.common;

import java.net.http.HttpClient;
import java.time.Duration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ExternalHttpClientConfig {
    /**
     * Single shared client for all outbound provider calls (embeddings, rerank,
     * HyDE). One instance means one connection pool, so keep-alive connections
     * to the same provider host are reused across components instead of each
     * component maintaining its own pool.
     */
    @Bean
    public HttpClient externalHttpClient() {
        return HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();
    }
}
