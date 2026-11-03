package com.jarvis.supplychain.exception;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.BeanPropertyBindingResult;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GlobalExceptionHandlerTest {

    private final GlobalExceptionHandler handler = new GlobalExceptionHandler();

    @Test
    void illegalStateReturnsSanitizedBadGatewayMessage() {
        ResponseEntity<Map<String, Object>> response =
                handler.handleIllegalState(new IllegalStateException("private upstream detail"));

        assertEquals(HttpStatus.BAD_GATEWAY, response.getStatusCode());
        assertEquals(false, response.getBody().get("success"));
        assertEquals("분석 처리 중 오류가 발생했습니다.", response.getBody().get("message"));
        assertFalse(response.getBody().toString().contains("private upstream detail"));
    }

    @Test
    void validationReturnsFirstFieldErrorAsBadRequest() {
        BeanPropertyBindingResult bindingResult = new BeanPropertyBindingResult(new Object(), "request");
        bindingResult.addError(new FieldError("request", "bodyText", "must not be blank"));
        bindingResult.addError(new FieldError("request", "sourceType", "must not be null"));
        MethodArgumentNotValidException exception = new MethodArgumentNotValidException(null, bindingResult);

        ResponseEntity<Map<String, Object>> response = handler.handleValidation(exception);

        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
        assertEquals("bodyText: must not be blank", response.getBody().get("message"));
        assertEquals(false, response.getBody().get("success"));
    }

    @Test
    void validationWithoutFieldErrorsUsesFallbackMessage() {
        BeanPropertyBindingResult bindingResult = new BeanPropertyBindingResult(new Object(), "request");
        MethodArgumentNotValidException exception = new MethodArgumentNotValidException(null, bindingResult);

        ResponseEntity<Map<String, Object>> response = handler.handleValidation(exception);

        assertEquals("Validation failed", response.getBody().get("message"));
        assertEquals(HttpStatus.BAD_REQUEST, response.getStatusCode());
    }

    @Test
    void genericExceptionReturnsSanitizedInternalServerError() {
        ResponseEntity<Map<String, Object>> response =
                handler.handleGeneric(new RuntimeException("database password or path"));

        assertEquals(HttpStatus.INTERNAL_SERVER_ERROR, response.getStatusCode());
        assertEquals(false, response.getBody().get("success"));
        assertEquals("내부 오류가 발생했습니다.", response.getBody().get("message"));
        assertTrue(response.getBody().size() == 2);
        assertFalse(response.getBody().toString().contains("database password or path"));
    }
}
