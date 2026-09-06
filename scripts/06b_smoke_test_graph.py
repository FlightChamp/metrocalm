"""
06b_smoke_test_graph.py
=======================
route_edge_mart / transfer_edge_mart 가 '실제로 옳은 그래프'인지 검증한다.

왜 필요한가
-----------
06 이 보고한 '강연결 True / 고립 노드 없음' 은 그래프가 끊기지 않았다는 뜻일 뿐,
엣지가 옳다는 뜻이 아니다. 예를 들어 '하남검단산 -> 둔촌동' 같은 존재하지 않는
구간이 섞여 있어도 강연결은 True 로 나온다.

그래서 실제 경로를 탐색해 다음을 확인한다.

  T1  지선 경로가 분기 노드를 경유하는가            (성수지선 / 신정지선 / 마천지선)
  T2  응암순환이 비대칭인가                          (응암->연신내 vs 연신내->응암)
  T3  존재하지 않아야 할 인접 구간이 없는가          (하남검단산-둔촌동, 신설동-도림천)
  T4  주요 환승역의 환승 엣지가 있는가                (서울역 1-4, 종로3가 1-3-5 등)
  T5  범위 밖 노선/역이 경로에 등장하지 않는가
  T6  대표 OD 10쌍의 최단경로가 상식과 맞는가
  T7  2호선 순환선 양방향이 모두 성립하는가

사용법
------
    python scripts/06b_smoke_test_graph.py --root .
"""

from __future__ import annotations

import argparse
import heapq
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ENC = "utf-8-sig"
VALID_LINES = {"1", "2", "3", "4", "5", "6", "7", "8"}


def load_mart(mart_dir: Path, name: str) -> pd.DataFrame:
    for ext in (".parquet", ".csv.gz"):
        p = mart_dir / f"{name}{ext}"
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    raise FileNotFoundError(f"{name} 을 찾을 수 없습니다. 06 스크립트를 먼저 실행하세요.")


class Graph:
    """이동시간 기준 최단경로 (혼잡도 미반영 — 그래프 구조 검증 전용)."""

    def __init__(self, ride: pd.DataFrame, transfer: pd.DataFrame):
        self.adj: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for r in ride.itertuples():
            self.adj[r.from_node].append((r.to_node, float(r.travel_time_min), "ride"))
        for r in transfer.itertuples():
            t = float(r.transfer_time_min)
            self.adj[r.from_node].append((r.to_node, t, "transfer"))
            self.adj[r.to_node].append((r.from_node, t, "transfer"))
        self.nodes = set(self.adj) | {v for lst in self.adj.values() for v, _, _ in lst}

    def shortest(self, src: str, dst: str):
        if src not in self.nodes or dst not in self.nodes:
            return None, float("inf")
        dist = {src: 0.0}
        prev: dict[str, tuple[str, str]] = {}
        pq = [(0.0, src)]
        seen = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == dst:
                break
            for v, w, kind in self.adj[u]:
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = (u, kind)
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None, float("inf")
        path, cur = [dst], dst
        while cur != src:
            cur, kind = prev[cur]
            path.append(cur)
        return list(reversed(path)), dist[dst]


def fmt(path: list[str] | None) -> str:
    if not path:
        return "(경로 없음)"
    return " → ".join(path)


class SmokeTest:
    def __init__(self, root: Path):
        self.root = root
        self.ride = load_mart(root / "data" / "marts", "route_edge_mart")
        self.transfer = load_mart(root / "data" / "marts", "transfer_edge_mart")
        self.g = Graph(self.ride, self.transfer)
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))

    # ---------- T1 지선 ----------
    def t1_branches(self) -> None:
        cases = [
            ("성수지선", "2_신설동", "2_건대입구", "@seongsu_branch"),
            # 까치산->영등포구청 은 5호선이 더 빨라 지선을 타지 않는다. 지선 내부 역을 기점으로 잡는다.
            ("신정지선", "2_도림천", "2_문래", "@sinjeong_branch"),
            ("마천지선", "5_마천", "5_광화문", "@macheon_branch"),
        ]
        for label, src, dst, marker in cases:
            path, t = self.g.shortest(src, dst)
            ok = path is not None and any(marker in n for n in path)
            self.check(f"T1 {label} 분기 노드 경유", ok,
                       f"{fmt(path)}  ({t:.1f}분)" if path else "경로 없음")

    # ---------- T2 응암순환 비대칭 ----------
    def t2_loop(self) -> None:
        p1, t1 = self.g.shortest("6_응암", "6_연신내")
        p2, t2 = self.g.shortest("6_연신내", "6_응암")
        ok = p1 and p2 and abs(t1 - t2) > 1.0 and len(p1) > len(p2)
        self.check("T2 응암순환 비대칭", bool(ok),
                   f"응암→연신내 {t1:.1f}분({len(p1)}역) / 연신내→응암 {t2:.1f}분({len(p2)}역)")

    # ---------- T3 존재하면 안 되는 엣지 ----------
    def t3_phantom(self) -> None:
        bad = [("5", "하남검단산", "둔촌동"), ("2", "신설동", "도림천"),
               ("5", "마천", "둔촌동"), ("2", "충정로", "용답")]
        found = []
        for line, a, b in bad:
            m = self.ride[(self.ride.line_id.astype(str) == line)
                          & (self.ride.from_station == a) & (self.ride.to_station == b)]
            if len(m):
                found.append(f"{line}호선 {a}-{b}")
        self.check("T3 유령 인접구간 없음", not found,
                   "없음" if not found else "발견: " + ", ".join(found))

    # ---------- T4 환승 엣지 ----------
    def t4_transfers(self) -> None:
        need = [("서울역", {"1", "4"}), ("종로3가", {"1", "3", "5"}),
                ("동대문역사문화공원", {"2", "4", "5"}), ("왕십리", {"2", "5"}),
                ("고속터미널", {"3", "7"}), ("까치산", {"2", "5"})]
        miss = []
        for st, lines in need:
            sub = self.transfer[self.transfer.station_name == st]
            have = set(sub.from_line.astype(str)) | set(sub.to_line.astype(str))
            if not lines.issubset(have):
                miss.append(f"{st}(있음:{sorted(have)} 필요:{sorted(lines)})")
        self.check("T4 주요 환승역 엣지", not miss,
                   "모두 정상" if not miss else "; ".join(miss))

    # ---------- T5 범위 밖 ----------
    def t5_scope(self) -> None:
        nodes = self.g.nodes
        bad = [n for n in nodes if n.split("_")[0] not in VALID_LINES]
        self.check("T5 범위 밖 노선 없음", not bad,
                   "없음" if not bad else str(sorted(bad)[:10]))

    # ---------- T6 대표 OD ----------
    def t6_od(self) -> pd.DataFrame:
        ods = [("2_신촌", "2_잠실"), ("1_서울역", "2_강남"), ("5_군자", "5_여의나루"),
               ("2_한양대", "3_고속터미널"), ("4_혜화", "2_사당"), ("1_종각", "6_이태원"),
               ("2_건대입구", "2_홍대입구"), ("6_안암", "2_삼성"), ("5_방화", "5_마천"),
               ("6_응암", "3_고속터미널")]
        rows = []
        for s, d in ods:
            p, t = self.g.shortest(s, d)
            # 환승 = 연속한 두 노드의 '역명'이 같은 경우 (호선 간 환승 + 분기 계통 환승)
            def station_of(node: str) -> str:
                return node.split("_", 1)[1].split("@")[0]
            n_tr = 0
            if p:
                for i in range(len(p) - 1):
                    if station_of(p[i]) == station_of(p[i + 1]):
                        n_tr += 1
            rows.append({"from": s, "to": d, "time_min": round(t, 1) if p else None,
                         "n_stops": len(p) - 1 if p else None, "n_transfer": n_tr,
                         "ok": p is not None})
        df = pd.DataFrame(rows)
        self.check("T6 대표 OD 10쌍 경로 산출", bool(df["ok"].all()),
                   f"성공 {int(df['ok'].sum())}/10")
        return df

    # ---------- T7 순환선 양방향 ----------
    def t7_loop2(self) -> None:
        p1, t1 = self.g.shortest("2_시청", "2_사당")
        p2, t2 = self.g.shortest("2_사당", "2_시청")
        ok = p1 and p2 and abs(t1 - t2) < 0.5
        self.check("T7 2호선 순환 양방향 대칭", bool(ok),
                   f"시청→사당 {t1:.1f}분 / 사당→시청 {t2:.1f}분")

    # ---------- 실행 ----------
    def run(self) -> int:
        self.t1_branches()
        self.t2_loop()
        self.t3_phantom()
        self.t4_transfers()
        self.t5_scope()
        od = self.t6_od()
        self.t7_loop2()

        print("=" * 78)
        print(" 06b_smoke_test_graph — 그래프 구조 검증")
        print("=" * 78)
        n_ok = 0
        for name, ok, detail in self.results:
            mark = "PASS" if ok else "FAIL"
            n_ok += ok
            print(f"  [{mark}] {name}")
            if detail:
                print(f"         {detail}")
        print(f"\n  통과: {n_ok}/{len(self.results)}")
        print("\n[대표 OD 최단경로 — 이동시간만 반영, 혼잡도 미반영]")
        print(od.to_string(index=False))
        print("=" * 78)
        return 0 if n_ok == len(self.results) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    return SmokeTest(Path(args.root)).run()


if __name__ == "__main__":
    sys.exit(main())
