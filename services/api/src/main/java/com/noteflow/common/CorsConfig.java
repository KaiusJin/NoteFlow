package com.noteflow.common;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class CorsConfig {
    private final String[] allowedOriginPatterns;

    public CorsConfig(@Value("${noteflow.cors.allowed-origin-patterns:http://localhost:*,http://127.0.0.1:*}") String origins) {
        this.allowedOriginPatterns = origins.lines()
            .flatMap(line -> java.util.Arrays.stream(line.split(",")))
            .map(String::trim)
            .filter(origin -> !origin.isEmpty())
            .toArray(String[]::new);
    }

    @Bean
    public WebMvcConfigurer corsConfigurer() {
        return new WebMvcConfigurer() {
            @Override
            public void addCorsMappings(CorsRegistry registry) {
                registry.addMapping("/**")
                    .allowedOriginPatterns(allowedOriginPatterns)
                    .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                    .allowedHeaders("*")
                    .exposedHeaders("X-Next-Cursor", RequestTraceFilter.TRACE_HEADER)
                    .allowCredentials(false)
                    .maxAge(3600);
            }
        };
    }
}
