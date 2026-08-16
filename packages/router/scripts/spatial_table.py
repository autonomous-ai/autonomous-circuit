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
        runs[data["summary"]["arm"]] = data
        rulers.add(data["measuredAgainst"]["rulerHashBefore"])
        rulers.add(data["measuredAgainst"]["rulerHashAfter"])
    if len(rulers) != 1:
        raise SystemExit(
            f"arms were measured against different rulers {sorted(rulers)} — "
            f"these are separate baselines, not a comparison"
        )
    ruler = rulers.pop()

    order = [a for a in ("relay", "spatial", "spatial-flat", "spatial-residue",
                         "spatial-tight") if a in runs] + [
        a for a in sorted(runs) if a not in
        ("relay", "spatial", "spatial-flat", "spatial-residue", "spatial-tight")
    ]

    print(f"ruler {ruler}   "
          f"head {runs[order[0]]['measuredAgainst']['gitHead']}"
          f"{' (dirty)' if runs[order[0]]['measuredAgainst']['gitDirty'] else ''}\n")
    head = (f"{'arm':<18}{'mean routed':>12}{'clean':>7}{'harness err':>12}"
            f"{'det':>6}{'id clashes':>11}{'seconds':>9}")
    print(head)
    print("-" * len(head))
    for arm in order:
        s = runs[arm]["summary"]
        det = (f"{s['deterministic']}/{s['instances']}"
               if s.get("deterministic") is not None else "n/m")
        print(f"{arm:<18}{s['meanCompleteness'] * 100:>11.1f}%"
              f"{s['cleanInstances']:>4}/{s['instances']:<3}"
              f"{s['harnessErrors']:>12}{det:>6}"
              f"{s['collidingCopperIds']:>11}{s['totalSeconds']:>9.0f}")
        if (s["meanCompletenessWithUniqueIds"] != s["meanCompleteness"]
                or s["harnessErrorsWithUniqueIds"] != s["harnessErrors"]):
            print(f"{'  ^ unique ids':<18}"
                  f"{s['meanCompletenessWithUniqueIds'] * 100:>11.1f}%"
                  f"{s['cleanInstancesWithUniqueIds']:>4}/{s['instances']:<3}"
                  f"{s['harnessErrorsWithUniqueIds']:>12}")

    print(f"\n{'instance':<48}" + "".join(f"{a:>17}" for a in order))
    per: dict[str, dict[str, float]] = {}
    for arm in order:
        for row in runs[arm]["rows"]:
            per.setdefault(row["instance"], {})[arm] = row["score"]["completeness"]
    for instance in sorted(per):
        cells = "".join(f"{per[instance].get(a, float('nan')) * 100:>16.1f}%"
                        for a in order)
        print(f"{instance:<48}{cells}")

    # Where did the regions actually do work?
    spatial_runs = [a for a in order if a.startswith("spatial")]
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
                    "rows": {a: runs[a]["rows"] for a in order},
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
