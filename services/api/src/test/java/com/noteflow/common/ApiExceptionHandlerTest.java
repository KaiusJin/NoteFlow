package com.noteflow.common;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

class ApiExceptionHandlerTest {
    @Test
    void preservesFrameworkHttpStatusInsteadOfConvertingItToServerError() {
        ApiExceptionHandler handler = new ApiExceptionHandler();

        var response = handler.handleFrameworkError(
            new ResponseStatusException(HttpStatus.BAD_REQUEST, "eventId is required")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().message()).isEqualTo("eventId is required");
    }
}
