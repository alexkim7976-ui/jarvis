"""
무료 데이터 소스 확장 검증 (네트워크 불필요 — 파싱/합의 로직만)

실행:
  .venv/bin/python test_free_providers.py
  .venv/bin/python -m pytest test_free_providers.py -v
"""
from __future__ import annotations

import time


def test_registry_has_free_providers():
    import data_providers as dp
    status = dp.provider_status()
    for name in ("stooq", "naver", "daum", "finnhub"):
        assert name in status, f"{name} 레지스트리 누락"
    # 키 불필요 소스는 기본 활성
    assert status["stooq"]["enabled"] is True
    assert status["naver"]["enabled"] is True
    assert status["daum"]["enabled"] is True
    print("✓ 레지스트리에 무료 소스 등록 + 무키 소스 활성")


def test_naver_number_parsing():
    from data_providers import NaverProvider
    n = NaverProvider
    assert n._num("25.02배") == 25.02
    assert n._num("12,372원") == 12372.0
    assert n._num("8.22") == 8.22
    assert n._num("46.76%") == 46.76
    assert n._num("-") is None
    assert n._num(None) is None
    print("✓ 네이버 숫자 파싱(배/원/%/콤마)")


def test_naver_market_cap_korean_units():
    from data_providers import NaverProvider
    n = NaverProvider
    # 1,809조 4,232억 = 1809e12 + 4232e8
    assert n._market_won("1,809조 4,232억") == 1809 * 1e12 + 4232 * 1e8
    assert n._market_won("15조") == 15 * 1e12
    assert n._market_won("4,232억") == 4232 * 1e8
    print("✓ 네이버 시총 조/억 단위 파싱")


def test_daum_number_parsing_and_sign():
    """다음 JSON은 changePrice/changeRate가 절대값 → change(RISE/FALL)로 부호 결정."""
    from data_providers import DaumProvider

    class _Resp:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return self._d

    import data_providers as dp
    orig = dp._http_get
    try:
        # 하락 케이스: 부호가 음수로 뒤집혀야 함
        dp._http_get = lambda *a, **k: _Resp({
            "name": "테스트", "tradePrice": 1000.0, "change": "FALL",
            "changePrice": 50.0, "changeRate": 0.05, "accTradeVolume": 12345,
            "per": 10.0, "pbr": 1.2, "eps": 100.0, "marketCap": 1e12,
            "openingPrice": 1040.0, "highPrice": 1050.0, "lowPrice": 990.0,
        })
        q = DaumProvider().quote("005930")
        assert q["success"] is True
        assert q["price"] == 1000.0
        assert q["change"] == -50.0, q["change"]
        assert abs(q["change_pct"] - (-5.0)) < 1e-9, q["change_pct"]
        assert q["volume"] == 12345
        assert q["per"] == 10.0 and q["pbr"] == 1.2
        # 상승 케이스: 양수 유지
        dp._http_get = lambda *a, **k: _Resp({
            "name": "테스트", "tradePrice": 2000.0, "change": "RISE",
            "changePrice": 100.0, "changeRate": 0.05, "accTradeVolume": 1,
        })
        q2 = DaumProvider().quote("005930")
        assert q2["change"] == 100.0 and abs(q2["change_pct"] - 5.0) < 1e-9
    finally:
        dp._http_get = orig
    print("✓ 다음 금융 파싱 + RISE/FALL 부호")


def test_stooq_symbol_mapping():
    from data_providers import StooqProvider
    s = StooqProvider._to_symbol
    assert s("AAPL") == "aapl.us"
    assert s("005930.KS") == "005930.kr"
    assert s("035420.KQ") == "035420.kr"
    print("✓ Stooq 심볼 매핑")


def test_resolve_tickers_includes_new_sources():
    from cross_validation import _resolve_tickers
    kr = _resolve_tickers("삼성전자")
    assert kr["_is_korean"] is True
    assert "naver" in kr and kr["naver"] == kr["kis"]
    assert "stooq" in kr
    us = _resolve_tickers("AAPL")
    assert us["_is_korean"] is False
    assert "finnhub" in us
    assert "stooq" in us
    print("✓ 티커 해석에 네이버/Stooq/Finnhub 포함")


def test_consensus_with_new_sources():
    """네이버+야후+KIS 3소스 가격 합의 + 네이버 단독 PER."""
    from cross_validation import StockData, cross_validate
    results = [
        StockData(source="Yahoo Finance", ticker="005930.KS", success=True,
                  price=72000.0, fetched_at=time.time()),
        StockData(source="네이버 금융", ticker="005930", success=True,
                  price=72100.0, per=13.5, pbr=1.4, eps=5300.0, fetched_at=time.time()),
        StockData(source="한국투자증권", ticker="005930", success=True,
                  price=71900.0, fetched_at=time.time()),
        # 이상치(1% 초과) — 제외돼야 함
        StockData(source="Stooq", ticker="005930.KS", success=True,
                  price=99999.0, fetched_at=time.time()),
    ]
    rep = cross_validate(results, "삼성전자")
    # 3개 정상치의 합의(72000 근방), 99999 이상치 제외
    assert rep.price.final is not None
    assert 71800 <= rep.price.final <= 72200, rep.price.final
    assert any(src == "Stooq" for src, *_ in rep.price.rejected), "이상치 미제외"
    # PER은 네이버만 제공 → 단일 소스로라도 채택
    assert rep.per.final == 13.5
    assert rep.pbr.final == 1.4
    print("✓ 다중소스 합의 + 이상치 제외 + 펀더멘털 채택")


def test_reliability_weighted_consensus():
    """가격 합의가 신뢰도 높은 소스(KIS 실시간)로 기운다."""
    from cross_validation import StockData, cross_validate
    results = [
        # 지연 애그리게이터(가중 0.70)
        StockData(source="Yahoo Finance", ticker="005930.KS", success=True,
                  price=72200.0, fetched_at=time.time()),
        # 브로커 실시간(가중 1.00)
        StockData(source="한국투자증권", ticker="005930", success=True,
                  price=72000.0, fetched_at=time.time()),
    ]
    rep = cross_validate(results, "삼성전자")
    # 단순평균이면 72100. 가중평균은 KIS(1.0)로 기울어 72100보다 낮아야 함.
    assert rep.price.final is not None
    assert 72000.0 <= rep.price.final < 72100.0, rep.price.final
    print("✓ 신뢰도 가중 합의(실시간 소스 우선)")


def test_two_source_dispersion_not_outlier():
    """2개 소스가 산정기준 차이로 갈려도 '전체 이상치'가 아니라 가중 추정."""
    from cross_validation import StockData, cross_validate
    results = [
        StockData(source="다음 금융", ticker="005930", success=True,
                  price=1000.0, per=43.3, fetched_at=time.time()),
        StockData(source="네이버 금융", ticker="005930", success=True,
                  price=1000.0, per=25.0, fetched_at=time.time()),
    ]
    rep = cross_validate(results, "삼성전자")
    # PER은 tol(5%)를 크게 넘겨 accepted가 비지만 final은 가중평균으로 산출돼야 함
    assert rep.per.final is not None
    assert "이상치" not in rep.per.method, rep.per.method
    assert 25.0 <= rep.per.final <= 43.3
    print("✓ 2소스 기준차 → 이상치 낙인 없이 가중 추정")


def test_price_confidence_levels():
    from cross_validation import StockData, cross_validate, price_confidence

    def mk(rows):
        return [StockData(source=s, ticker="X", success=True, price=p,
                          fetched_at=time.time()) for s, p in rows]
    r4 = cross_validate(mk([("한국투자증권", 1000), ("네이버 금융", 1001),
                            ("다음 금융", 999), ("Yahoo Finance", 1000)]), "X")
    assert price_confidence(r4)["level"] == "매우 높음"
    r3 = cross_validate(mk([("네이버 금융", 1000), ("다음 금융", 1001),
                            ("Yahoo Finance", 999)]), "X")
    assert price_confidence(r3)["level"] == "높음"
    r2 = cross_validate(mk([("네이버 금융", 1000), ("Yahoo Finance", 1001)]), "X")
    assert price_confidence(r2)["level"] == "보통"
    r1 = cross_validate(mk([("Yahoo Finance", 1000)]), "X")
    assert price_confidence(r1)["level"] == "낮음"
    print("✓ 가격 신뢰도 등급(4/3/2/1 소스)")


def test_briefing_confidence_caution():
    from cross_validation import StockData, cross_validate, format_briefing
    r2 = cross_validate([
        StockData(source="네이버 금융", ticker="005930", success=True,
                  price=1000.0, currency="KRW", fetched_at=time.time()),
        StockData(source="Yahoo Finance", ticker="005930.KS", success=True,
                  price=1001.0, currency="KRW", fetched_at=time.time()),
    ], "삼성전자")
    b = format_briefing(r2)
    assert "신뢰도는 보통" in b, b
    print("✓ 보통 신뢰도 시 음성 브리핑 경고")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"✗ {fn.__name__}: {e}")
    print("\n" + "=" * 50)
    print(f"결과: {passed}/{len(fns)} passed")
    return passed == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
