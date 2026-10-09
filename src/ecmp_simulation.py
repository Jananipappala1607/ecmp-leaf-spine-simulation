"""
ECMP Load Distribution and Failure Recovery in a Leaf-Spine Fabric
------------------------------------------------------------------
Scenarios: 1) Baseline  2) Single-link failure  3) High-traffic stress
           4) Stress + link failure

Run:   python src/ecmp_simulation.py
Output: results/figures/*.png  and  results/data/*.csv
"""

import csv
import hashlib
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

import matplotlib
import matplotlib.pyplot as plt

# ---------------------------------------------------------------
# Output folders (project_root/results/...)
# ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
FIG_DIR = ROOT / "results" / "figures"
DATA_DIR = ROOT / "results" / "data"
FIG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

SHOW_PLOTS = False          # set True to open plot windows
if not SHOW_PLOTS:
    matplotlib.use("Agg")

# ---------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------
LEAVES = ["L1", "L2", "L3", "L4"]
SPINES = ["S1", "S2", "S3"]
LINK_CAPACITY = 15.0        # Gbps per leaf-spine link
FLOW_RATE = 1.0             # Gbps per flow

links = {tuple(sorted((l, s))): LINK_CAPACITY for l in LEAVES for s in SPINES}

# (source, destination, total traffic Gbps, number of flows)
BASELINE_TRAFFIC = [("L1", "L4", 12, 12), ("L2", "L3", 9, 9),
                    ("L1", "L3", 6, 6), ("L2", "L4", 6, 6)]
STRESS_TRAFFIC = [("L1", "L4", 30, 30), ("L2", "L3", 25, 25),
                  ("L1", "L3", 20, 20), ("L2", "L4", 20, 20)]

FAILED_LINK = tuple(sorted(("L1", "S2")))


# ---------------------------------------------------------------
# ECMP logic
# ---------------------------------------------------------------
def find_ecmp_paths(source, destination, failed_links=None):
    failed_links = failed_links or set()
    paths = []
    for spine in SPINES:
        l1 = tuple(sorted((source, spine)))
        l2 = tuple(sorted((spine, destination)))
        if l1 not in failed_links and l2 not in failed_links:
            paths.append((source, spine, destination))
    return paths


def ecmp_hash(source, destination, flow_number):
    flow_id = f"{source}-{destination}-{flow_number}"
    return int(hashlib.md5(flow_id.encode()).hexdigest(), 16)


def jain_fairness(values):
    values = [float(v) for v in values]
    if not values:
        return 0.0
    sq = sum(v * v for v in values)
    if sq == 0:
        return 1.0
    return sum(values) ** 2 / (len(values) * sq)


def simulate_network(traffic_demands, failed_links=None):
    failed_links = failed_links or set()
    link_usage = defaultdict(float)
    path_usage = defaultdict(float)
    total_offered = 0.0
    without_path = 0.0

    for src, dst, total, n_flows in traffic_demands:
        total_offered += total
        paths = find_ecmp_paths(src, dst, failed_links)
        if not paths:
            without_path += total
            continue
        for flow in range(1, n_flows + 1):
            path = paths[ecmp_hash(src, dst, flow) % len(paths)]
            path_usage[path] += FLOW_RATE
            for a, b in zip(path, path[1:]):
                link_usage[tuple(sorted((a, b)))] += FLOW_RATE

    for link in links:
        link_usage.setdefault(link, 0.0)

    util = {l: link_usage[l] / LINK_CAPACITY * 100 for l in links}
    vals = list(util.values())
    overloaded = [l for l in links if link_usage[l] > LINK_CAPACITY]

    return {
        "link_usage": dict(link_usage),
        "path_usage": dict(path_usage),
        "utilization": util,
        "total_offered_traffic": total_offered,
        "traffic_without_path": without_path,
        "max_utilization": max(vals),
        "min_utilization": min(vals),
        "average_utilization": mean(vals),
        "utilization_std": pstdev(vals),
        "overloaded_count": len(overloaded),
        "link_fairness": jain_fairness(vals),
        "path_fairness": jain_fairness([v for v in path_usage.values() if v > 0]),
    }


def calculate_recovery(before, after, failed_link):
    affected = before["link_usage"].get(failed_link, 0.0)
    lost = after["traffic_without_path"]
    recovered = max(affected - lost, 0.0)
    pct = recovered / affected * 100 if affected > 0 else 100.0
    return {"affected": affected, "recovered": recovered, "lost": lost,
            "percentage": pct,
            "failed_link_after": after["link_usage"].get(failed_link, 0.0)}


# ---------------------------------------------------------------
# Console output
# ---------------------------------------------------------------
def print_experiment(name, result, failed_links):
    print("\n" + "#" * 78)
    print(f"EXPERIMENT: {name}")
    print("#" * 78)
    print("Failed links:", ", ".join("-".join(l) for l in sorted(failed_links)) or "None")

    print("\nECMP flow distribution")
    for path in sorted(result["path_usage"]):
        print(f"  {' -> '.join(path):18s}: {result['path_usage'][path]:5.1f} Gbps")

    print("\nLink utilization")
    for link in sorted(links):
        use = result["link_usage"][link]
        status = "OVERLOADED" if use > LINK_CAPACITY else "OK"
        print(f"  {link[0]}-{link[1]}: {use:5.1f}/{LINK_CAPACITY:.0f} Gbps "
              f"({result['utilization'][link]:6.1f}%) [{status}]")

    print("\nMetrics")
    print(f"  Offered traffic      : {result['total_offered_traffic']:.1f} Gbps")
    print(f"  Traffic without path : {result['traffic_without_path']:.1f} Gbps")
    print(f"  Min / Max / Avg util : {result['min_utilization']:.2f}% / "
          f"{result['max_utilization']:.2f}% / {result['average_utilization']:.2f}%")
    print(f"  Std deviation        : {result['utilization_std']:.2f}")
    print(f"  Link Jain fairness   : {result['link_fairness']:.4f}")
    print(f"  Path Jain fairness   : {result['path_fairness']:.4f}")
    print(f"  Overloaded links     : {result['overloaded_count']}")


# ---------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------
def finish(filename):
    plt.tight_layout()
    plt.savefig(FIG_DIR / filename, dpi=300, bbox_inches="tight")
    if SHOW_PLOTS:
        plt.show()
    plt.close()


def bar_chart(labels, values, title, ylabel, filename, size=(12, 6),
              limit_line=False, fmt="{:.1f}%", offset=2, rotation=20, ylim=None):
    plt.figure(figsize=size)
    bars = plt.bar(labels, values)
    if limit_line:
        plt.axhline(100, linestyle="--", linewidth=2, label="Capacity limit (100%)")
        plt.legend()
    plt.title(title, fontsize=15, fontweight="bold")
    plt.ylabel(ylabel)
    plt.xticks(rotation=rotation)
    plt.grid(axis="y", alpha=0.3)
    if ylim:
        plt.ylim(*ylim)
    for bar, v in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, v + offset, fmt.format(v),
                 ha="center", fontsize=9)
    finish(filename)


def architecture_diagram():
    plt.figure(figsize=(12, 7))
    spine_pos = {"S1": (2, 3), "S2": (6, 3), "S3": (10, 3)}
    leaf_pos = {"L1": (1, 0), "L2": (4, 0), "L3": (7, 0), "L4": (10, 0)}
    for leaf, (lx, ly) in leaf_pos.items():
        for spine, (sx, sy) in spine_pos.items():
            if tuple(sorted((leaf, spine))) == FAILED_LINK:
                plt.plot([lx, sx], [ly, sy], "--", linewidth=2)
            else:
                plt.plot([lx, sx], [ly, sy], linewidth=1.5)
    for pos in (spine_pos, leaf_pos):
        for name, (x, y) in pos.items():
            plt.scatter(x, y, s=1600, marker="s", zorder=3)
            plt.text(x, y, name, ha="center", va="center", fontsize=12,
                     fontweight="bold", zorder=4)
    plt.text(6, 3.7, "SPINE LAYER", ha="center", fontsize=14, fontweight="bold")
    plt.text(5.5, -0.7, "LEAF LAYER", ha="center", fontsize=14, fontweight="bold")
    plt.title("ECMP Leaf-Spine Fabric Architecture (dashed = failed link L1-S2)",
              fontsize=15, fontweight="bold")
    plt.xlim(-0.5, 11)
    plt.ylim(-1.2, 4.5)
    plt.axis("off")
    finish("ecmp_leaf_spine_architecture.png")


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
def main():
    scenarios = [
        ("Baseline ECMP", BASELINE_TRAFFIC, set()),
        ("Single-Link Failure", BASELINE_TRAFFIC, {FAILED_LINK}),
        ("High-Traffic Stress", STRESS_TRAFFIC, set()),
        ("Stress + Link Failure", STRESS_TRAFFIC, {FAILED_LINK}),
    ]

    print("*" * 78)
    print(" ECMP LOAD DISTRIBUTION AND FAILURE RECOVERY - LEAF-SPINE FABRIC")
    print("*" * 78)
    print(f"Leaves: {LEAVES}  Spines: {SPINES}  Links: {len(links)}  "
          f"Capacity: {LINK_CAPACITY:.0f} Gbps  Flow: {FLOW_RATE:.0f} Gbps")

    architecture_diagram()

    results = {}
    for name, traffic, failed in scenarios:
        results[name] = simulate_network(traffic, failed)
        print_experiment(name, results[name], failed)

    baseline = results["Baseline ECMP"]
    failure = results["Single-Link Failure"]
    rec = calculate_recovery(baseline, failure, FAILED_LINK)

    print("\n" + "=" * 78)
    print("FAILURE RECOVERY EVALUATION")
    print("=" * 78)
    print(f"Failed link            : {FAILED_LINK[0]}-{FAILED_LINK[1]}")
    print(f"Traffic affected       : {rec['affected']:.1f} Gbps")
    print(f"Traffic recovered      : {rec['recovered']:.1f} Gbps")
    print(f"Traffic without path   : {rec['lost']:.1f} Gbps")
    print(f"Recovery percentage    : {rec['percentage']:.1f}%")
    print(f"Failed link after      : {rec['failed_link_after']:.1f} Gbps")
    print("Capacity-feasible      :",
          "SUCCESS" if failure["max_utilization"] <= 100 else "CONGESTION")

    # ---- summary table + CSVs -----------------------------------
    print("\n" + "=" * 100)
    print("FINAL EXPERIMENT SUMMARY")
    print("=" * 100)
    print(f"{'Scenario':24s}{'Traffic':>9s}{'Max':>9s}{'Avg':>9s}"
          f"{'StdDev':>9s}{'Fairness':>10s}{'Overload':>10s}")
    summary_rows = []
    for name, r in results.items():
        print(f"{name:24s}{r['total_offered_traffic']:>9.1f}"
              f"{r['max_utilization']:>8.1f}%{r['average_utilization']:>8.1f}%"
              f"{r['utilization_std']:>9.2f}{r['link_fairness']:>10.4f}"
              f"{r['overloaded_count']:>10d}")
        summary_rows.append({
            "Scenario": name,
            "Offered Traffic (Gbps)": r["total_offered_traffic"],
            "Max Utilization (%)": round(r["max_utilization"], 2),
            "Average Utilization (%)": round(r["average_utilization"], 2),
            "Std Dev (%)": round(r["utilization_std"], 2),
            "Link Fairness": round(r["link_fairness"], 4),
            "Path Fairness": round(r["path_fairness"], 4),
            "Overloaded Links": r["overloaded_count"],
            "Recovery (%)": round(rec["percentage"], 1) if name == "Single-Link Failure" else "",
        })

    with open(DATA_DIR / "ecmp_experiment_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
        w.writeheader()
        w.writerows(summary_rows)

    with open(DATA_DIR / "ecmp_link_utilization.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Scenario", "Link", "Traffic_Gbps", "Capacity_Gbps",
                    "Utilization_Percent", "Status"])
        for name, r in results.items():
            for link in sorted(links):
                use = r["link_usage"][link]
                w.writerow([name, f"{link[0]}-{link[1]}", round(use, 2),
                            LINK_CAPACITY, round(r["utilization"][link], 2),
                            "OVERLOADED" if use > LINK_CAPACITY else "OK"])

    # ---- graphs --------------------------------------------------
    link_labels = [f"{a}-{b}" for a, b in sorted(links)]
    names = list(results.keys())

    bar_chart(link_labels, [baseline["utilization"][l] for l in sorted(links)],
              "Baseline ECMP Link Utilization", "Utilization (%)",
              "01_baseline_link_utilization.png", size=(13, 6),
              limit_line=True, rotation=45)

    # before vs after failure (grouped bars)
    x = list(range(len(link_labels)))
    wd = 0.38
    plt.figure(figsize=(14, 6))
    plt.bar([i - wd / 2 for i in x], [baseline["utilization"][l] for l in sorted(links)],
            width=wd, label="Before failure")
    plt.bar([i + wd / 2 for i in x], [failure["utilization"][l] for l in sorted(links)],
            width=wd, label="After L1-S2 failure")
    plt.axhline(100, linestyle="--", linewidth=2, label="Capacity limit")
    plt.title("Link Utilization Before and After L1-S2 Failure",
              fontsize=15, fontweight="bold")
    plt.xlabel("Leaf-Spine Link")
    plt.ylabel("Utilization (%)")
    plt.xticks(x, link_labels, rotation=45)
    plt.legend()
    plt.grid(axis="y", alpha=0.3)
    finish("02_before_after_failure.png")

    bar_chart(names, [results[n]["max_utilization"] for n in names],
              "Maximum Link Utilization Across Experiments",
              "Maximum Utilization (%)", "03_max_utilization_scenarios.png",
              limit_line=True, offset=3)

    bar_chart(names, [results[n]["link_fairness"] for n in names],
              "ECMP Link-Utilization Fairness", "Jain's Fairness Index",
              "04_fairness_comparison.png", fmt="{:.3f}", offset=0.02,
              ylim=(0, 1.05))

    l1l4 = {" -> ".join(p): v for p, v in baseline["path_usage"].items()
            if p[0] == "L1" and p[-1] == "L4"}
    bar_chart(list(l1l4.keys()), list(l1l4.values()),
              "ECMP Flow Distribution for L1 to L4 (Baseline)", "Traffic (Gbps)",
              "05_ecmp_path_distribution.png", size=(10, 6), fmt="{:.0f}", offset=0.2)

    bar_chart(names, [results[n]["overloaded_count"] for n in names],
              "Number of Overloaded Links", "Number of Overloaded Links",
              "06_overloaded_links.png", fmt="{}", offset=0.1)

    print(f"\nFigures saved in: {FIG_DIR}")
    print(f"CSV files saved in: {DATA_DIR}")


if __name__ == "__main__":
    main()
