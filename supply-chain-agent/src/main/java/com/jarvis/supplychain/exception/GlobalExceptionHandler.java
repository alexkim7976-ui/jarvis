package com.jarvis.supplychain.exception;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Map;

/**
 * 전역 예외 처리. 내부 메시지(스택트레이스·라이브러리 버전·Gemini 원문·내부 경로 등)는
 * 서버 로그에만 남기고, 클라이언트에는 정제된 일반 메시지만 반환한다(정보 노출/Footprinting 차단).
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(IllegalStateException.class)
    public ResponseEntity<Map<String, Object>> handleIllegalState(IllegalStateException ex) {
        // 상세는 로그에만. 서비스 계층이 이미 사용자용으로 정제한 메시지만 전달한다.
        log.error("처리 실패(IllegalState): {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(Map.of(
                "success", false,
                "message", "분석 처리 중 오류가 발생했습니다."
        ));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, Object>> handleValidation(MethodArgumentNotValidException ex) {
        // 클라이언트 자신의 요청 검증 오류만 노출(필드명/사유). 내부 정보 아님.
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(e -> e.getField() + ": " + e.getDefaultMessage())
                .orElse("Validation failed");
        return ResponseEntity.badRequest().body(Map.of("success", false, "message", msg));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> handleGeneric(Exception ex) {
        // 예상치 못한 예외 — 스택/내부 경로가 기본 에러페이지로 새지 않도록 일반 메시지로 마스킹.
        log.error("처리되지 않은 예외", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(Map.of(
                "success", false,
                "message", "내부 오류가 발생했습니다."
        ));
    }
}
