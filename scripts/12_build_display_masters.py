"""
12_build_display_masters.py
===========================
사용자 UI 용 **역 단위(station-level)** 마스터를 만든다.

핵심 원칙
---------
여유로 서울 은 사용자에게 역 단위 입력을 제공하지만, 내부 그래프와 혼잡도 계산은
호선별 station_uid 단위로 유지한다. 환승역에서 어떤 노선을 처음 탈지는 사용자가
고정하지 않는 한 알고리즘이 후보로 비교하며, 최초 승차 노선 선택은 환승으로
계산하지 않는다.

  사용자가 보는 것 : "서울역", "종로3가"
  내부가 쓰는 것   : 1_서울역, 4_서울역 / 1_종로3가, 3_종로3가, 5_종로3가

출력
----
    data/master/station_display_master.csv   역 단위 선택용 마스터

노선도 좌표는 이 스크립트가 만들지 않는다.
좌표 워크북(5120x2880)이 source of truth 이고 14_import_map_workbook.py 가 가져온다.

좌표 생성 방식
--------------
실제 위경도가 아니라 schematic 좌표다. route_edge_mart 의 승차 엣지로 그래프를
만들고 Kamada-Kawai 레이아웃을 돌린다. 노선이 사슬 형태라 자연스럽게 선으로
펼쳐지고, 2호선 순환선은 고리로 나타난다. 수작업 배치가 필요하면 이 파일을
직접 편집하면 된다(스크립트는 기존 좌표를 --keep-coords 로 보존한다).

사용법
------
    python scripts/12_build_display_masters.py --root .
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

    def __init__(self, root: Path):
        self.root = root
        self.marts = root / "data" / "marts"
        self.master = root / "data" / "master"
        self.master.mkdir(parents=True, exist_ok=True)
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

    # ---------- 실행 ----------
    def run(self):
        disp = self.build_display()
        p1 = self.master / "station_display_master.csv"
        disp.to_csv(p1, index=False, encoding=ENC)

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
        print("\n  노선도 좌표는 14_import_map_workbook.py 가 좌표 워크북에서 만든다.")
        print("=" * 84)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    DisplayMasterBuilder(Path(args.root)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
