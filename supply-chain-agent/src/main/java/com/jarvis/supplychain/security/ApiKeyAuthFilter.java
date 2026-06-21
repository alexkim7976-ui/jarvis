package com.jarvis.supplychain.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

/**
 * 공유 시크릿(X-API-Key) 기반 인증 필터.
 *
 * <p>방어 계층:
 * <ul>
 *   <li>1차 — server.address=127.0.0.1 로 LAN 외부 노출 차단(application.yml).</li>
 *   <li>2차 — /health 외 모든 요청에 일치하는 X-API-Key 헤더 요구.</li>
 * </ul>
 * 제로 트러스트 정책: 키가 미설정/빈 값이면 인증을 건너뛰지 않고 필터 초기화 단계에서
 * {@link IllegalStateException} 을 던져 애플리케이션 기동 자체를 차단한다(Fail-Fast).
 */
@Component
public class ApiKeyAuthFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(ApiKeyAuthFilter.class);
    private static final String HEADER = "X-API-Key";

    private final String configuredKey;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public ApiKeyAuthFilter(@Value("${supply-chain.security.api-key:}") String configuredKey) {
        this.configuredKey = configuredKey == null ? "" : configuredKey.trim();
        if (this.configuredKey.isBlank()) {
            // Fail-Fast: 키 없이는 빈(bean) 생성 → 스프링 컨텍스트 기동을 모두 중단.
            throw new IllegalStateException(
                    "SUPPLY_CHAIN_API_KEY 미설정 — 제로 트러스트 정책상 API 키 없이는 기동할 수 없습니다. "
                    + "환경변수 SUPPLY_CHAIN_API_KEY 를 설정한 뒤 다시 시작하세요.");
        }
        log.info("API 키 인증 활성화 — /health 외 모든 엔드포인트에 X-API-Key 헤더 필요");
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path != null && path.endsWith("/health"); // 헬스체크만 공개
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String provided = request.getHeader(HEADER);
        if (provided != null && constantTimeEquals(provided, configuredKey)) {
            chain.doFilter(request, response);
            return;
        }
        response.setStatus(HttpStatus.UNAUTHORIZED.value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(objectMapper.writeValueAsString(Map.of(
                "success", false,
                "message", "Unauthorized"
        )));
    }

    private static boolean constantTimeEquals(String a, String b) {
        byte[] x = a.getBytes(StandardCharsets.UTF_8);
        byte[] y = b.getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(x, y);
    }
}
