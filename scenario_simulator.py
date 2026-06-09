"""
J.A.R.V.I.S — What-If 시나리오 시뮬레이터 (구조적 충격 전파 엔진)
────────────────────────────────────────────────────────────
가상 시나리오(예: "대만 규모 7.0 지진으로 TSMC 2주 가동중단")를 받아
명시적 공급망 그래프 위에서 충격을 전파시키고, 관심종목별 영향을 산출한다.

기존 supply-chain-agent(Gemini 추론, 뉴스 원문 입력)는 '수동·정성' 분석이고,
이 모듈은 '능동·구조적' 분석이다 — 숫자(impactScore)가 그래프에서 결정론적으로
나오므로 방어 가능하다. 출력은 supply_chain_module.py의 valueChainImpacts 포맷
(impactScore -5~+5, impactType)과 호환되어 HUD/LLM 경로를 그대로 재사용한다.

전파 모델
  · 한 충격이 부호가 다른 여러 채널로 분기하고, 전파되며 종류가 바뀐다.
      SUPPLY_OUTAGE → 하류 S−(공급부족) · 상류 D−(수요급감) · 대체 D+(반사이익)
  · 채널 점프는 경로당 jump_budget회로 제한(조합 폭발 차단).
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


# ── 노드 / 그래프 ──────────────────────────────────────────────
@dataclass
class Node:
    ticker: str
    name: str
    country: str                  # "TW" | "KR" | "US" ...
    role: str                     # FOUNDRY | OSAT | EQUIPMENT | FABLESS | SUBSTRATE ...
    watchlist: bool = False
    matrix_base: int = 50         # 평시 매트릭스 총점(0~100); --live 시 stock_quant로 교체
    inventory_days: float = 0.0   # 재고 완충 (공급부족 흡수)
    spare_capacity: float = 0.0   # 반사이익 흡수 여력 (0~1)
    pricing_power: float = 0.5    # 원가 전가력 (0~1)
    quote_symbol: str = ""        # yfinance 심볼 (예: 042700.KQ, TSM); 비면 ticker 사용


class SupplyGraph:
    """경량 방향성 다중관계 그래프 (networkx 불필요). A→B[SUPPLIES] = A가 B에 공급(A=상류)."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self._out: dict[str, list] = defaultdict(list)   # src -> [(dst, rel, w)]
        self._in: dict[str, list] = defaultdict(list)    # dst -> [(src, rel, w)]

    def add(self, node: Node) -> None:
        self.nodes[node.ticker] = node

    def link(self, src: str, dst: str, rel: str, weight: float) -> None:
        self._out[src].append((dst, rel, weight))
        self._in[dst].append((src, rel, weight))

    def neighbors(self, node: str, rel: str, direction: str):
        # down = SUPPLIES 정방향(고객) / up = 역방향(공급사) / in = 역방향(대체사)
        src = self._out[node] if direction == "down" else self._in[node]
        return [(other, w) for (other, r, w) in src if r == rel]


# ── 충격 정의 ──────────────────────────────────────────────────
@dataclass
class Shock:
    origin: str                   # 진앙 노드 (REGULATORY는 predicate 사용)
    kind: str                     # SUPPLY_OUTAGE | PRICE_SPIKE | DEMAND_COLLAPSE | CAPEX_BOOM | REGULATORY
    intensity: float = 1.0        # 0~1 (1.0 = 완전 중단/붕괴, 0.5 = 부분)
    duration_weeks: float = 2.0
    predicate: object = None      # REGULATORY: Callable[[Node], bool]


# 전파 중인 충격 kind: 자기연쇄(rel, dir, sign), 게이팅 속성, 점프룰(rel,dir,kind)
KIND = {
    "S_MINUS": {"rel": "SUPPLIES", "dir": "down", "sign": -1, "gate": "inventory_days",
                "jump": ("SUBSTITUTE", "in", "D_PLUS")},   # 공급부족 → 대체사 수혜(점프)
    "D_MINUS": {"rel": "SUPPLIES", "dir": "up",   "sign": -1, "gate": None, "jump": None},
    "D_PLUS":  {"rel": "SUPPLIES", "dir": "up",   "sign": +1, "gate": "spare_capacity", "jump": None},
    "COST_UP": {"rel": "SUPPLIES", "dir": "down", "sign": -1, "gate": "pricing_power",
                "jump": ("SUBSTITUTE", "in", "D_PLUS")},   # 원가급등 → 대체재 수혜(점프)
}

# 충격 종류 → 1차 주입 채널 [(direction, rel, arriving_kind)]
SEED = {
    "SUPPLY_OUTAGE":   [("down", "SUPPLIES", "S_MINUS"),
                        ("up", "SUPPLIES", "D_MINUS"),
                        ("in", "SUBSTITUTE", "D_PLUS")],
    "PRICE_SPIKE":     [("down", "SUPPLIES", "COST_UP"),
                        ("in", "SUBSTITUTE", "D_PLUS")],
    "DEMAND_COLLAPSE": [("up", "SUPPLIES", "D_MINUS")],
    "CAPEX_BOOM":      [("up", "SUPPLIES", "D_PLUS"),
                        ("in", "SUBSTITUTE", "D_MINUS")],
    "REGULATORY":      [("up", "SUPPLIES", "D_MINUS"),
                        ("in", "SUBSTITUTE", "D_PLUS")],
}

# 진앙 자기 타격 부호 (호재성 충격만 +)
SELF_SIGN = {"CAPEX_BOOM": +1}


def _gate(node: Node, gate, mag: float, shock: Shock) -> float:
    """게이팅: 재고 완충 / 반사이익 여력."""
    if gate == "inventory_days" and node.inventory_days > 0:
        cover = min(1.0, (shock.duration_weeks * 7.0) / node.inventory_days)
        return mag * cover                       # 재고로 흡수 → 잔여 충격만 통과
    if gate == "spare_capacity":
        return min(mag, node.spare_capacity)     # 반사이익은 가동 여력 한도
    return mag


def _origins(g: SupplyGraph, shock: Shock):
    if shock.kind == "REGULATORY" and callable(shock.predicate):
        return [t for t, n in g.nodes.items() if shock.predicate(n)]
    return [shock.origin]


def propagate(g: SupplyGraph, shock: Shock, *, max_hops=3, decay=0.5, jump_budget=1, eps=0.05):
    """충격을 전파해 노드별 누적 impact(부호 있는 raw)와 경로를 반환."""
    impact: dict[str, float] = defaultdict(float)
    paths: dict[str, list] = defaultdict(list)
    q: deque = deque()
    intensity = abs(shock.intensity)
    self_sign = SELF_SIGN.get(shock.kind, -1)

    for origin in _origins(g, shock):                       # 1차 주입
        if origin in g.nodes:
            impact[origin] += intensity * self_sign         # 진앙 직격
        for direction, rel, kind in SEED[shock.kind]:
            for nbr, w in g.neighbors(origin, rel, direction):
                q.append((origin, nbr, kind, intensity * w, 1, 0))

    while q:                                                # 2차+ 연쇄
        src, node, kind, mag, hop, jumps = q.popleft()
        if abs(mag) < eps or node not in g.nodes:
            continue
        spec = KIND[kind]
        if kind == "COST_UP":                               # 가격 전가 특례 (보존)
            pp = g.nodes[node].pricing_power
            impact[node] += -(mag * (1.0 - pp))             # 흡수분 = 마진 타격
            carry = mag * pp                                # 전가분만 하류로
        else:
            carry = _gate(g.nodes[node], spec["gate"], mag, shock)
            impact[node] += carry * spec["sign"]
        paths[node].append((src, kind, round(carry * (spec["sign"] if kind != "COST_UP" else -1), 2)))

        if hop >= max_hops:
            continue
        for nbr, w in g.neighbors(node, spec["rel"], spec["dir"]):       # 동일종류 연쇄
            q.append((node, nbr, kind, carry * w * decay, hop + 1, jumps))
        jr = spec["jump"]
        if jr and jumps < jump_budget and abs(carry) > 2 * eps:          # 채널 점프(예산 한도)
            j_rel, j_dir, j_kind = jr
            for nbr, w in g.neighbors(node, j_rel, j_dir):
                q.append((node, nbr, j_kind, abs(carry) * w * decay, hop + 1, jumps + 1))
    return impact, paths


# ── 출력: supply_chain_module valueChainImpacts 포맷 (-5~+5) ──────
SCALE = 5.0   # raw 1.0(직격) ≈ impactScore 5


def to_value_chain_impacts(g: SupplyGraph, impact, paths, *, watchlist_only=True):
    rows = []
    for ticker, raw in impact.items():
        node = g.nodes.get(ticker)
        if node is None or (watchlist_only and not node.watchlist):
            continue
        score = max(-5, min(5, round(raw * SCALE)))
        if score == 0:
            continue
        rows.append({
            "ticker": ticker,
            "companyName": node.name,
            "sector": node.role,
            "impactScore": score,
            "impactType": "BENEFICIARY" if score > 0 else "VICTIM",
            "matrixBase": node.matrix_base,
            "matrixAfter": max(0, min(100, node.matrix_base + score * 2)),
            "path": paths.get(ticker, []),
        })
    rows.sort(key=lambda r: r["impactScore"])     # 최악 → 최선
    return rows


def simulate(g: SupplyGraph, shock: Shock, **kw) -> dict:
    """엔진 → supply_chain_module 호환 결과 dict."""
    impact, paths = propagate(g, shock, **kw)
    impacts = to_value_chain_impacts(g, impact, paths)
    return {
        "success": True,
        "scenario": f"{shock.kind} @ {shock.origin} (강도 {shock.intensity}, {shock.duration_weeks}주)",
        "valueChainImpacts": impacts,
        "overlay_table": {
            "type": "table",
            "title": f"What-If · {shock.origin} {shock.kind}",
            "headers": ["기업", "티커", "영향", "점수", "섹터"],
            "rows": [[r["companyName"], r["ticker"], r["impactType"], f'{r["impactScore"]:+d}', r["sector"]]
                     for r in impacts],
        },
    }


# ── 실데이터 연결: 평시 matrix_base를 stock_quant 매트릭스 총점으로 교체 ──
def attach_matrix_scores(g: SupplyGraph, score_fn=None, *, watchlist_only=True, verbose=True) -> dict:
    """노드 matrix_base를 실데이터(stock_quant 총점 0~100)로 교체. 실패 노드는 시드값 유지."""
    if score_fn is None:
        from stock_quant import get_comprehensive_matrix_score as score_fn  # 지연 임포트(무거움)
    updated, fell_back = [], []
    for ticker, node in g.nodes.items():
        if watchlist_only and not node.watchlist:
            continue
        sym = node.quote_symbol or ticker
        try:
            res = score_fn(sym)
            total = res.get("total_score") if isinstance(res, dict) else None
            if res.get("success") and isinstance(total, (int, float)):
                node.matrix_base = int(total)
                updated.append((node.name, sym, node.matrix_base))
            else:
                fell_back.append((node.name, sym, str(res.get("message", "total_score 없음"))[:50]))
        except Exception as e:
            fell_back.append((node.name, sym, str(e)[:50]))
    if verbose:
        for n, s, sc in updated:
            print(f"  ✓ {n}({s}) matrix_base={sc}")
        for n, s, msg in fell_back:
            print(f"  · {n}({s}) 시드 유지 — {msg}")
    return {"updated": updated, "fell_back": fell_back}


# ── 시드 그래프: 국내 반도체 밸류체인 (큐레이션 예시) ──────────────────
def build_demo_graph() -> SupplyGraph:
    g = SupplyGraph()
    g.add(Node("TSMC", "TSMC", "TW", "FOUNDRY", quote_symbol="TSM"))              # 진앙
    g.add(Node("042700", "한미반도체", "KR", "EQUIPMENT", True, 78, 5, 0.10, 0.6, "042700.KQ"))
    g.add(Node("240810", "원익IPS", "KR", "EQUIPMENT", True, 61, 5, 0.10, 0.5, "240810.KQ"))
    g.add(Node("067310", "하나마이크론", "KR", "OSAT", True, 64, 15, 0.60, 0.4, "067310.KQ"))  # 반사이익(A기업)
    g.add(Node("036540", "SFA반도체", "KR", "OSAT", True, 55, 15, 0.20, 0.4, "036540.KQ"))
    g.add(Node("131970", "두산테스나", "KR", "OSAT", True, 58, 15, 0.15, 0.4, "131970.KQ"))
    g.add(Node("108320", "LX세미콘", "KR", "FABLESS", True, 60, 21, 0.20, 0.5, "108320.KQ"))   # 하류 고객(웨이퍼 부족)
    g.add(Node("123860", "아나패스", "KR", "FABLESS", True, 47, 15, 0.30, 0.5, "123860.KQ"))   # LX 경쟁사(점프 수혜)
    g.add(Node("222800", "심텍", "KR", "SUBSTRATE", True, 52, 20, 0.30, 0.4, "222800.KQ"))     # OSAT 공급사(2차 수혜)

    # SUPPLIES: 상류 → 하류
    g.link("042700", "TSMC", "SUPPLIES", 0.60)     # 한미반도체 → TSMC (후공정 장비)
    g.link("240810", "TSMC", "SUPPLIES", 0.30)     # 원익IPS → TSMC
    g.link("TSMC", "108320", "SUPPLIES", 0.55)     # TSMC → LX세미콘 (파운드리 웨이퍼)
    g.link("222800", "067310", "SUPPLIES", 0.40)   # 심텍 → 하나마이크론 (기판)
    g.link("222800", "036540", "SUPPLIES", 0.25)   # 심텍 → SFA반도체

    # SUBSTITUTE: 대체사 → 원본
    g.link("067310", "TSMC", "SUBSTITUTE", 0.70)   # 하나마이크론이 TSMC 후공정 capa 대체
    g.link("036540", "TSMC", "SUBSTITUTE", 0.35)
    g.link("131970", "TSMC", "SUBSTITUTE", 0.25)
    g.link("123860", "108320", "SUBSTITUTE", 0.70) # 아나패스가 LX세미콘(DDI) 대체
    return g


def _print(result: dict) -> None:
    print(f"\n[시나리오] {result['scenario']}")
    print("─" * 72)
    print(f"{'기업':<10}{'티커':<9}{'영향':<12}{'점수':>5}  {'매트릭스':>9}   경로")
    print("─" * 72)
    for r in result["valueChainImpacts"]:
        chain = " ← ".join(f"{src}:{k}" for src, k, _ in r["path"][:2])
        mtx = f"{r['matrixBase']:>3}→{r['matrixAfter']:<3}"
        print(f"{r['companyName']:<10}{r['ticker']:<9}{r['impactType']:<12}"
              f"{r['impactScore']:>+5}  {mtx:>9}   {chain}")
    print("─" * 72)
    vics = [r for r in result["valueChainImpacts"] if r["impactScore"] < 0]
    bens = [r for r in result["valueChainImpacts"] if r["impactScore"] > 0]
    if vics:
        w = vics[0]
        print(f"최대 타격 : {w['companyName']}({w['ticker']}) {w['impactScore']:+d}")
    if bens:
        b = bens[-1]
        print(f"최대 수혜 : {b['companyName']}({b['ticker']}) {b['impactScore']:+d}")


if __name__ == "__main__":
    import sys
    g = build_demo_graph()
    if "--live" in sys.argv:                       # 평시 matrix_base를 실데이터로 교체
        print("[실데이터] stock_quant 매트릭스 총점으로 matrix_base 교체 중...")
        attach_matrix_scores(g)
    # "대만 규모 7.0 지진으로 TSMC 2주 가동중단"
    quake = Shock(origin="TSMC", kind="SUPPLY_OUTAGE", intensity=1.0, duration_weeks=2.0)
    _print(simulate(g, quake))
