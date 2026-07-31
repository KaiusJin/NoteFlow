package com.noteflow.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class RequestTraceFilter extends OncePerRequestFilter {
    static final String TRACE_HEADER = "X-Trace-Id";

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain chain) throws ServletException, IOException {
        String incoming = request.getHeader(TRACE_HEADER);
        String traceId = validTraceId(incoming) ? incoming : UUID.randomUUID().toString();
        response.setHeader(TRACE_HEADER, traceId);
        try (MDC.MDCCloseable ignored = MDC.putCloseable("traceId", traceId)) {
            chain.doFilter(request, response);
        }
    }

    private boolean validTraceId(String value) {
        return value != null && value.length() >= 8 && value.length() <= 128
            && value.matches("[A-Za-z0-9._:-]+");
    }
}
