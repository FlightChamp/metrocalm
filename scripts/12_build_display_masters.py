"""
12_build_display_masters.py
===========================
사용자 UI 용 **역 단위(station-level)** 마스터를 만든다.

핵심 원칙
---------
MetroCalm 은 사용자에게 역 단위 입력을 제공하지만, 내부 그래프와 혼잡도 계산은
호선별 station_uid 단위로 유지한다. 환승역에서 어떤 노선을 처음 탈지는 사용자가
고정하지 않는 한 알고리즘이 후보로 비교하며, 최초 승차 노선 선택은 환승으로
계산하지 않는다.

  사용자가 보는 것 : "서울역", "종로3가"
  내부가 쓰는 것   : 1_서울역, 4_서울역 / 1_종로3가, 3_종로3가, 5_종로3가

출력
----
    data/master/station_display_master.csv   역 단위 선택용 마스터
    data/master/station_map_layout.csv       노선도 렌더링용 좌표

좌표 생성 방식
--------------
실제 위경도가 아니라 schematic 좌표다. route_edge_mart 의 승차 엣지로 그래프를
만들고 Kamada-Kawai 레이아웃을 돌린다. 노선이 사슬 형태라 자연스럽게 선으로
펼쳐지고, 2호선 순환선은 고리로 나타난다. 수작업 배치가 필요하면 이 파일을
직접 편집하면 된다(스크립트는 기존 좌표를 --keep-coords 로 보존한다).

사용법
------
    python scripts/12_build_display_masters.py --root .
    python scripts/12_build_display_masters.py --root . --keep-coords
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENC = "utf-8-sig"

LINE_COLORS = {
    "1": "#0052A4", "2": "#009D3E", "3": "#EF7C1C", "4": "#00A5DE",
    "5": "#996CAC", "6": "#CD7C2F", "7": "#747F00", "8": "#E6186C",
}


def load_mart(base: Path, name: str):
    for ext in (".parquet", ".csv.gz", ".csv"):
        p = base / (name + ext)
        if p.exists():
            return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
    return None


def station_of(node: str) -> str:
    return node.split("_", 1)[1].split("@")[0]


def line_of(node: str) -> str:
    return node.split("_", 1)[0]


class DisplayMasterBuilder:

    def __init__(self, root: Path, keep_coords: bool):
        self.root = root
        self.marts = root / "data" / "marts"
        self.master = root / "data" / "master"
        self.master.mkdir(parents=True, exist_ok=True)
        self.keep_coords = keep_coords
        self.notes: list[str] = []

    # ---------- station_display_master ----------
    def build_display(self) -> pd.DataFrame:
        ride = load_mart(self.marts, "route_edge_mart")
        if ride is None:
            raise FileNotFoundError("route_edge_mart 가 없습니다. 06 을 먼저 실행하세요.")

        nodes = sorted(set(ride["from_node"]) | set(ride["to_node"]))
        rows = {}
        for n in nodes:
            key = station_of(n)
            line = line_of(n)
            r = rows.setdefault(key, {"station_key": key, "station_name": key,
                                      "lines": set(), "uids": []})
            r["lines"].add(line)
            r["uids"].append(n)

        # 환승역 / 분기역 / 이벤트역 플래그
        tr = load_mart(self.master, "transfer_station_master")
        transfer_names = set(tr["station_name"]) if tr is not None else set()
        br = load_mart(self.master, "branch_node_master")
        branch_names = set(br["station_name"]) if br is not None else set()
        evm = load_mart(self.master, "event_station_master")
        event_names = set(evm["station_name"]) if evm is not None else set()

        out = []
        for key, r in rows.items():
            lines = sorted(r["lines"], key=int)
            # 지선 노드는 뒤로 보내 대표 노드가 본선이 되게 한다
            uids = sorted(set(r["uids"]), key=lambda x: ("@" in x, x))
            out.append({
                "station_key": key,
                "station_name": key,
                "display_name": key if key.endswith("역") else key + "역",
                "available_lines": ",".join(lines),
                "candidate_station_uids": ";".join(uids),
                "n_lines": len(lines),
                "is_transfer_station": key in transfer_names,
                "is_branch_node": key in branch_names,
                "is_event_station": key in event_names,
            })
        df = pd.DataFrame(out).sort_values("station_key").reset_index(drop=True)
        self.notes.append("역 단위 %d개 (내부 노드 %d개)" % (len(df), len(nodes)))
        self.notes.append("환승역 %d / 분기역 %d / 이벤트 민감역 %d"
                          % (int(df.is_transfer_station.sum()),
                             int(df.is_branch_node.sum()),
                             int(df.is_event_station.sum())))
        return df

    # ---------- 좌표 ----------
    def build_layout(self, display: pd.DataFrame) -> pd.DataFrame:
        import networkx as nx
        ride = load_mart(self.marts, "route_edge_mart")

        # 역 단위 무향 그래프 (지선 노드는 본선 역명으로 합친다)
        G = nx.Graph()
        for r in ride.itertuples():
            a, b = station_of(r.from_node), station_of(r.to_node)
            if a == b:
                continue
            G.add_edge(a, b, line=str(r.line_id))

        pos_prev = {}
        prev_path = self.master / "station_map_layout.csv"
        if self.keep_coords and prev_path.exists():
            prev = pd.read_csv(prev_path)
            pos_prev = {r.map_station_key: (r.map_x, r.map_y) for r in prev.itertuples()
                        if pd.notna(r.map_x)}
            self.notes.append("기존 좌표 %d개 보존(--keep-coords)" % len(pos_prev))

        try:
            pos = nx.kamada_kawai_layout(G)
            algo = "kamada_kawai"
        except Exception:
            pos = nx.spring_layout(G, seed=42, iterations=300)
            algo = "spring"
        self.notes.append("좌표 알고리즘: %s (schematic, 실제 위경도 아님)" % algo)

        xs = np.array([p[0] for p in pos.values()])
        ys = np.array([p[1] for p in pos.values()])
        def scale(v, arr):
            lo, hi = arr.min(), arr.max()
            return float((v - lo) / (hi - lo) * 100.0) if hi > lo else 50.0

        rep_line = {}
        for r in ride.itertuples():
            for n in (r.from_node, r.to_node):
                rep_line.setdefault(station_of(n), str(r.line_id))

        rows = []
        for _, d in display.iterrows():
            k = d["station_key"]
            if k in pos_prev:
                x, y = pos_prev[k]
            elif k in pos:
                x, y = scale(pos[k][0], xs), scale(pos[k][1], ys)
            else:
                x, y = np.nan, np.nan
            rows.append({
                "map_station_key": k,
                "station_name": d["station_name"],
                "display_name": d["display_name"],
                "available_lines": d["available_lines"],
                "candidate_station_uids": d["candidate_station_uids"],
                "representative_line_id": rep_line.get(k, d["available_lines"].split(",")[0]),
                "branch_code": "branch" if d["is_branch_node"] else "main",
                "map_x": round(x, 3) if pd.notna(x) else np.nan,
                "map_y": round(y, 3) if pd.notna(y) else np.nan,
                "label_dx": 0.0, "label_dy": 1.2,
                "is_transfer_station": d["is_transfer_station"],
                "is_branch_node": d["is_branch_node"],
                "is_event_station": d["is_event_station"],
                "display_priority": (1 if d["is_transfer_station"] else
                                     (2 if d["is_event_station"] else 3)),
            })
        lay = pd.DataFrame(rows)
        n_missing = int(lay["map_x"].isna().sum())
        if n_missing:
            self.notes.append("좌표 미배치 %d개 (지도에 표시되지 않음)" % n_missing)
        return lay

    # ---------- 실행 ----------
    def run(self):
        disp = self.build_display()
        lay = self.build_layout(disp)

        p1 = self.master / "station_display_master.csv"
        p2 = self.master / "station_map_layout.csv"
        disp.to_csv(p1, index=False, encoding=ENC)
        lay.to_csv(p2, index=False, encoding=ENC)

        print("=" * 84)
        print(" 12_build_display_masters - 완료")
        print("=" * 84)
        for n in self.notes:
            print("  " + n)
        print("\n[역 단위 마스터 샘플]")
        sample = disp[disp.station_key.isin(["서울역", "종로3가", "강남", "성수", "까치산"])]
        print(sample[["station_key", "display_name", "available_lines",
                      "candidate_station_uids", "is_transfer_station"]].to_string(index=False))
        print("\n[노선 수 분포]")
        print(disp["n_lines"].value_counts().sort_index().to_string())
        print("\n[생성 파일]")
        print("  station_display_master  %s (%d행)" % (p1, len(disp)))
        print("  station_map_layout      %s (%d행)" % (p2, len(lay)))
        print("=" * 84)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--keep-coords", action="store_true",
                    help="기존 station_map_layout.csv 의 좌표를 보존한다(수작업 배치 후 사용)")
    args = ap.parse_args(argv)
    DisplayMasterBuilder(Path(args.root), args.keep_coords).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
