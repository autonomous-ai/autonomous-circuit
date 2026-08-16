#!/usr/bin/env python3.12
"""Put the spatial arms and the relay in one table, on one ruler.

Refuses to print a comparison whose arms were measured against different
rulers. Two scores are comparable only when their check sets match, and a table
is the place that rule is easiest to break silently.

    python3.12 packages/router/scripts/spatial_table.py work/spatial/*.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    runs = {}
    rulers = set()
    for path in args.files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        arm = data["summary"]["arm"]
        # Two files can carry the same arm — a one-run pass and a two-run
        # determinism pass. Keep the one that actually measured determinism,
        # whatever order the shell globbed them in.
        seen = runs.get(arm)
        if seen and (seen["summary"].get("runs") or 1) >= (
            data["summary"].get("runs") or 1
        ):
            continue
        runs[arm] = data
        rulers.add(data["measuredAgainst"]["rulerHashBefore"])
        rulers.add(data["measuredAgainst"]["rulerHashAfter"])
    if len(rulers) != 1:
        raise SystemExit(
            f"arms were measured against different rulers {sorted(rulers)} — "
            f"these are separate baselines, not a comparison"
        )
    ruler = rulers.pop()

    # Floor first, then the number to beat, then the arms in the order they
    # were argued for. Anything unknown lands at the end, sorted.
    known = ("single", "relay", "spatial", "spatial-flat", "spatial-tight",
             "spatial-chain", "spatial-escape-first", "spatial-residue",
             "spatial-best", "spatial-shuffled")
    order = [a for a in known if a in runs] + [
        a for a in sorted(runs) if a not in known
    ]

    print(f"ruler {ruler}   "
          f"head {runs[order[0]]['measuredAgainst']['gitHead']}"
          f"{' (dirty)' if runs[order[0]]['measuredAgainst']['gitDirty'] else ''}\n")
    head = (f"{'arm':<18}{'mean routed':>12}{'nets':>10}{'clean':>7}"
            f"{'harness err':>12}{'det':>6}{'id clashes':>11}{'seconds':>9}")
    print(head)
    print("-" * len(head))
    print("  mean routed is the mean of per-instance completeness; nets is the\n"
          "  pooled count. They disagree when an arm wins on small boards, and\n"
          "  clean — boards at 100% — is the one the fab-ready bar reads.")
    for arm in order:
        s = runs[arm]["summary"]
        got = sum(r["score"]["connectedNets"] for r in runs[arm]["rows"])
        want = sum(r["score"]["routableNets"] for r in runs[arm]["rows"])
        det = (f"{s['deterministic']}/{s['instances']}"
               if s.get("deterministic") is not None else "n/m")
        print(f"{arm:<18}{s['meanCompleteness'] * 100:>11.1f}%"
              f"{got:>6}/{want:<3}"
              f"{s['cleanInstances']:>4}/{s['instances']:<3}"
              f"{s['harnessErrors']:>12}{det:>6}"
              f"{s['collidingCopperIds']:>11}{s['totalSeconds']:>9.0f}")
        if (s["meanCompletenessWithUniqueIds"] != s["meanCompleteness"]
                or s["harnessErrorsWithUniqueIds"] != s["harnessErrors"]):
            print(f"{'  ^ unique ids':<18}"
                  f"{s['meanCompletenessWithUniqueIds'] * 100:>11.1f}%"
                  f"{s['cleanInstancesWithUniqueIds']:>4}/{s['instances']:<3}"
                  f"{s['harnessErrorsWithUniqueIds']:>12}")

    print(f"\n{'instance':<44}" + "".join(f"{a[-13:]:>15}" for a in order))
    per: dict[str, dict[str, float]] = {}
    for arm in order:
        for row in runs[arm]["rows"]:
            per.setdefault(row["instance"], {})[arm] = row["score"]["completeness"]
    for instance in sorted(per):
        cells = "".join(f"{per[instance].get(a, float('nan')) * 100:>14.1f}%"
                        for a in order)
        print(f"{instance:<44}{cells}")

    # Where did the regions actually do work?
    # The best-scoring spatial arm, because that is the configuration anyone
    # would run; the others are in the record.
    spatial_runs = sorted(
        (a for a in order if a.startswith("spatial")),
        key=lambda a: -runs[a]["summary"]["meanCompleteness"],
    )
    if spatial_runs:
        arm = spatial_runs[0]
        print(f"\nper-region assignment and completeness ({arm})")
        for row in runs[arm]["rows"]:
            part = row["spatial"]["partition"]
            stages = {s["stage"]: s for s in row["spatial"]["stages"]}
            if not part["seam"]:
                print(f"  {row['instance']:<48} no seam — {part['why']}")
                continue
            bits = []
            for region in part["regions"]:
                stage = stages.get(region["id"])
                added = f"+{stage['added_nets']}" if stage else "skipped"
                bits.append(
                    f"{region['id']}:{region['character']}"
                    f"/{region['expert'].split('-')[0]}"
                    f"/{len(region['interior_nets'])}n/{added}"
                )
            cross = stages.get("crossing")
            lead = (f"crossing +{cross['added_nets']}/{cross['asked_nets']}"
                    if cross else "crossing: none")
            print(f"  {row['instance']:<48} {lead}  " + "  ".join(bits))

    # Does the expert table earn its place? Same partition, same regions, same
    # order in both arms, so a per-character hit rate is a like-for-like
    # comparison of who routed the region.
    if "spatial" in runs and "spatial-flat" in runs:
        print("\nnets connected / nets asked, by region character")
        print(f"{'character':<14}{'expert':>26}{'flat (global)':>16}")
        tally: dict[str, dict[str, list[int]]] = {}
        for arm in ("spatial", "spatial-flat"):
            for row in runs[arm]["rows"]:
                for stage in row["spatial"]["stages"]:
                    got = tally.setdefault(stage["scope"], {}).setdefault(arm, [0, 0])
                    got[0] += stage["added_nets"]
                    got[1] += stage["asked_nets"]
        for scope in sorted(tally):
            expert = tally[scope].get("spatial", [0, 0])
            flat = tally[scope].get("spatial-flat", [0, 0])
            print(
                f"{scope:<14}"
                f"{expert[0]:>13}/{expert[1]:<12}"
                f"{flat[0]:>8}/{flat[1]:<7}"
            )

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "schema": "routerlib/spatial-comparison@1",
                    "ruler": ruler,
                    "arms": {a: runs[a]["summary"] for a in order},
                    "measuredAgainst": runs[order[0]]["measuredAgainst"],
                    "perInstanceCompleteness": per,
                    "rows": {a: [_slim(r) for r in runs[a]["rows"]]
                             for a in order},
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


def _slim(row: dict) -> dict:
    """The row without the per-pad lists.

    A region carries every pad id it owns, which is the right thing in the
    working file and 1.9MB of noise in a committed record. Everything a reader
    needs to re-derive a number stays: the score, the stages, and each region's
    id, character, expert and interior-net count.
    """
    out = {k: v for k, v in row.items() if k not in ("spatial", "fingerprints")}
    # The ruler is identical on every row by construction — the table refuses
    # to print otherwise — so it belongs once at the top, not 160 times.
    out["score"] = {k: v for k, v in row["score"].items() if k != "measuredAgainst"}
    if "spatial" not in row:
        return out
    part = row["spatial"]["partition"]
    out["spatial"] = {
        "stages": row["spatial"]["stages"],
        "notes": row["spatial"]["notes"],
        "partition": {
            **{k: v for k, v in part.items() if k not in ("regions", "crossingNets")},
            "crossingNetCount": len(part["crossingNets"]),
            "regions": [
                {
                    "id": r["id"],
                    "character": r["character"],
                    "expert": r["expert"],
                    "componentCount": r["component_count"],
                    "padCount": r["pad_count"],
                    "areaMm2": r["area_mm2"],
                    "padDensityPerCm2": r["pad_density_per_cm2"],
                    "finestPitchMm": r["finest_pitch_mm"],
                    "gridScore": r["grid_score"],
                    "bbox": r["bbox"],
                    "interiorNetCount": len(r["interior_nets"]),
                }
                for r in part["regions"]
            ],
        },
    }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
