#!/usr/bin/env python3
"""Does a router route *better* when it is told the truth about a pad?

Two rescore files, one ruler. ``--before`` is the tournament's copper — routed
against the inscribed stadium — re-scored against the true shape model.
``--after`` is copper produced by the same families, same budget, same seed,
routing against the true shape model from the start.

Both columns are measured with the same instrument, so the difference is the
routers' behaviour and nothing else. This is the question the pad model was
worth fixing for: scoring honestly only tells us how wrong we were, while
routing honestly is supposed to make the boards better.

    python3.12 packages/router/scripts/rerun_table.py \\
        --before work/tournament/rescore.json \\
        --after  work/tournament-truepads/rescore.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (c["router"], c["instance"]): c["new"]
        for c in data["cells"]
        if c.get("new")
    }, data


def kicad_real(root: Path) -> dict:
    """``(router, board) -> KiCad copper findings``, control already subtracted.

    Only boards whose placement still matches the instance are here:
    ``verify_real_board.py`` refuses the rest rather than judging a router
    against a board it never saw.
    """
    out: dict = {}
    if not root.is_dir():
        return out
    control: dict = {}
    rows: dict = {}
    for path in sorted(root.glob("*/*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if not row.get("ok"):
            continue
        count = (row.get("kicad") or {}).get("copperFindingCount")
        if count is None:
            continue
        if path.stem == "_empty-control":
            control[path.parent.name] = count
            out[("_empty-control", path.parent.name)] = count
        else:
            rows[(path.stem, path.parent.name)] = count
    for (router, board), count in rows.items():
        out[(router, board)] = count - control.get(board, 0)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default=str(REPO / "work" / "tournament" / "rescore.json"))
    ap.add_argument("--after", default=str(REPO / "work" / "tournament-truepads" / "rescore.json"))
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    before, bmeta = load(Path(args.before))
    after, ameta = load(Path(args.after))
    if bmeta["newRulerHash"] != ameta["newRulerHash"]:
        print("REFUSING: the two runs were scored with different rulers")
        print(f"  before {bmeta['newRulerHash']}  after {ameta['newRulerHash']}")
        return 2
    print(f"ruler {', '.join(ameta['newRulerHash'])} on both sides")
    print("before: copper routed against the inscribed stadium, re-scored")
    print("after : copper routed against the true shape model\n")

    families = sorted({r for r, _ in after})
    rows = {}
    hdr = (f"{'family':<24}{'routed% b':>10}{'routed% a':>10}{'clean b':>9}"
           f"{'clean a':>9}{'err b':>7}{'err a':>7}{'vias b':>8}{'vias a':>8}")
    print(hdr)
    print("-" * len(hdr))
    for family in families:
        keys = sorted(i for r, i in after if r == family and (family, i) in before)
        if not keys:
            continue
        b = [before[(family, i)] for i in keys]
        a = [after[(family, i)] for i in keys]
        n = len(keys)
        rows[family] = {
            "instances": n,
            "routedBefore": sum(x["completeness"] for x in b) / n,
            "routedAfter": sum(x["completeness"] for x in a) / n,
            "cleanBefore": sum(1 for x in b if x["clean"]),
            "cleanAfter": sum(1 for x in a if x["clean"]),
            "errorsBefore": sum(x["errors"] for x in b),
            "errorsAfter": sum(x["errors"] for x in a),
            "viasBefore": sum(x["vias"] for x in b),
            "viasAfter": sum(x["vias"] for x in a),
            "copperBefore": round(sum(x["copperMm"] for x in b), 1),
            "copperAfter": round(sum(x["copperMm"] for x in a), 1),
            "netsBefore": sum(x["connectedNets"] for x in b),
            "netsAfter": sum(x["connectedNets"] for x in a),
            "routable": sum(x["routableNets"] for x in a),
        }
        v = rows[family]
        print(f"{family:<24}{v['routedBefore'] * 100:>10.1f}{v['routedAfter'] * 100:>10.1f}"
              f"{v['cleanBefore']:>6}/{n:<2}{v['cleanAfter']:>6}/{n:<2}"
              f"{v['errorsBefore']:>7}{v['errorsAfter']:>7}"
              f"{v['viasBefore']:>8}{v['viasAfter']:>8}")

    print("\nNets connected, out of routable:")
    for family, v in rows.items():
        print(f"  {family:<24}{v['netsBefore']:>5} -> {v['netsAfter']:<5} of {v['routable']}")

    real = kicad_real(Path(args.after).parent / "verify-real")
    if real:
        print("\nKiCad on the real board, empty-solution control subtracted.")
        print("This is the column that is not our own harness agreeing with itself:")
        boards = sorted({b for _, b in real if _ == "_empty-control"})
        print(f"  {len(boards)} boards: {', '.join(boards)}")
        for family in families:
            got = [(b, v) for (r, b), v in sorted(real.items()) if r == family]
            if not got:
                continue
            total = sum(v for _, v in got)
            print(f"  {family:<24}{total:>5} copper findings over {len(got)} boards")

    print("\nPer instance, where the error count moved by more than 2:")
    for family in families:
        for i in sorted(x for r, x in after if r == family and (family, x) in before):
            b, a = before[(family, i)], after[(family, i)]
            if abs(a["errors"] - b["errors"]) <= 2:
                continue
            print(f"  {family:<24}{i:<46}{b['errors']:>4} -> {a['errors']:<4}"
                  f"  routed {b['completeness'] * 100:5.1f} -> {a['completeness'] * 100:5.1f}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"ruler": ameta["newRulerHash"], "perFamily": rows}, indent=1)
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
