package com.noteflow.security;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;

import com.noteflow.workspace.InternalWorkspacePrincipal;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

class InternalServiceAuthenticationFilterTest {
    private static final String TOKEN = "0123456789abcdef0123456789abcdef";

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void rejectsMissingToken() throws Exception {
        InternalServiceAuthenticationFilter filter = new InternalServiceAuthenticationFilter(TOKEN);
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/study/quiz-generations");
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, (ignoredRequest, ignoredResponse) -> {
            throw new AssertionError("Rejected request must not reach the controller");
        });

        assertEquals(401, response.getStatus());
    }

    @Test
    void authenticatesTokenAndWorkspaceTogether() throws Exception {
        UUID workspaceId = UUID.randomUUID();
        InternalServiceAuthenticationFilter filter = new InternalServiceAuthenticationFilter(TOKEN);
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/internal/study/quiz-generations");
        request.addHeader(InternalServiceAuthenticationFilter.TOKEN_HEADER, TOKEN);
        request.addHeader(InternalServiceAuthenticationFilter.WORKSPACE_HEADER, workspaceId.toString());
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicReference<Authentication> observed = new AtomicReference<>();

        filter.doFilter(request, response,
            (ignoredRequest, ignoredResponse) -> observed.set(SecurityContextHolder.getContext().getAuthentication()));

        InternalWorkspacePrincipal principal = assertInstanceOf(
            InternalWorkspacePrincipal.class, observed.get().getPrincipal());
        assertEquals(workspaceId, principal.workspaceId());
        assertEquals(200, response.getStatus());
    }

    @Test
    void authenticatesWorkerOnPublicFeatureEndpointWhenHeadersArePresent() throws Exception {
        UUID workspaceId = UUID.randomUUID();
        InternalServiceAuthenticationFilter filter = new InternalServiceAuthenticationFilter(TOKEN);
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/study/quiz-generations");
        request.addHeader(InternalServiceAuthenticationFilter.TOKEN_HEADER, TOKEN);
        request.addHeader(InternalServiceAuthenticationFilter.WORKSPACE_HEADER, workspaceId.toString());
        MockHttpServletResponse response = new MockHttpServletResponse();
        AtomicReference<Authentication> observed = new AtomicReference<>();

        filter.doFilter(request, response,
            (ignoredRequest, ignoredResponse) -> observed.set(SecurityContextHolder.getContext().getAuthentication()));

        InternalWorkspacePrincipal principal = assertInstanceOf(
            InternalWorkspacePrincipal.class, observed.get().getPrincipal());
        assertEquals(workspaceId, principal.workspaceId());
        assertEquals(200, response.getStatus());
    }
}
