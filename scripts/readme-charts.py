"""Render README figures from every row of the current leaderboard, offline.

Run from the repository root:
uv run --no-project --with matplotlib scripts/readme-charts.py
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import NullLocator

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/assets/readme"
NAMES = {
    "gemini": "Gemini 3.8 Flash",
    "astra": "GPT-6 Astra",
    "fable": "Claude Fable 5.1",
    "muse": "Muse Spark 1.3*",
    "glm": "GLM 5.3 Flash",
    "sol": "GPT-5.6 Sol",
    "deepseek": "DeepSeek V4.1 Flash",
    "terra": "GPT-5.6 Terra",
    "luna": "GPT-5.6 Luna",
}
BUCKETS = ["exact", "same_genus", "same_family", "other_family", "failed"]
THEMES = {
    "light": {
        "text": "#1f2328",
        "muted": "#59636e",
        "grid": "#d1d9e0",
        "bar": "#818b98",
        "colors": ["#16804a", "#74ad90", "#bed7c9", "#a69d95", "#c06b53"],
    },
    "dark": {
        "text": "#f0f6fc",
        "muted": "#b1bac4",
        "grid": "#30363d",
        "bar": "#9198a1",
        "colors": ["#71dca4", "#4d9873", "#355942", "#a69d95", "#ed967b"],
    },
}


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect():
    leaderboard = json.loads((ROOT / "data/benchmarks/leaderboard.json").read_text())
    rows, task_snapshot = [], None
    for entry in leaderboard:
        run = ROOT / "data/benchmarks/runs" / entry["run_id"]
        tasks_bytes = (run / "tasks.jsonl").read_bytes()
        digest = hashlib.sha256(tasks_bytes).hexdigest()
        if task_snapshot is None:
            task_snapshot = digest
        assert digest == task_snapshot, "README figures require a shared task snapshot"
        tasks = lines(run / "tasks.jsonl")
        predictions = {row["task_id"]: row for row in lines(run / "predictions.jsonl")}
        scores = json.loads((run / "scores.json").read_text())
        manifest = json.loads((run / "manifest.json").read_text())
        assert scores["tasks"] == entry["tasks"] == len(tasks)
        assert scores["top1"] == entry["top1"]
        assert scores["errors"] == entry["errors"]
        assert manifest["estimated_cost_usd"] == entry["cost_usd"]
        families = {task["correct_taxon"]: task["meta"]["family"] for task in tasks}
        counts = Counter({key: 0 for key in BUCKETS})
        for task in tasks:
            prediction = predictions.get(task["task_id"])
            if prediction is None or prediction["status"] != "answered":
                counts["failed"] += 1
                continue
            name = prediction["predictions"][0]["taxon"]
            accepted = [task["correct_taxon"], *task.get("synonyms_accepted", [])]
            accepted_names = {
                " ".join(value.strip().lower().split()) for value in accepted if value
            }
            if " ".join(name.strip().lower().split()) in accepted_names:
                counts["exact"] += 1
            elif name.split()[0] == task["correct_taxon"].split()[0]:
                counts["same_genus"] += 1
            elif families.get(name) == task["meta"]["family"]:
                counts["same_family"] += 1
            else:
                counts["other_family"] += 1
        assert sum(counts.values()) == len(tasks)
        assert counts["exact"] == round(entry["top1"] * len(tasks))
        assert counts["failed"] == scores["errors"] + scores["missing"]
        rows.append(
            {**entry, "label": NAMES.get(entry["model"], entry["model"]), "counts": dict(counts)}
        )
    assert len({row["run_id"] for row in rows}) == len(leaderboard)
    return {
        "tasks": len(tasks),
        "taxa": len(families),
        "tasksHash": scores["tasks_hash"],
        "tasksFileSha256": task_snapshot,
        "runs": rows,
    }


def chart(data, kind, theme, mobile):
    rows = data["runs"]
    width = 360 if mobile else 720
    height = len(rows) * (34 if mobile else 29) + (85 if kind == "mistakes" else 55)
    fig, ax = plt.subplots(figsize=(width / 72, height / 72))
    fig.subplots_adjust(
        left=0.025, right=0.975, top=0.98, bottom=75 / height if kind == "mistakes" else 42 / height
    )
    ax.set_facecolor("none")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_yticks([])
    ax.set_ylim(len(rows) - 0.25, -0.6)
    ax.tick_params(axis="x", length=0, colors=theme["muted"], labelsize=10)
    ax.grid(axis="x", color=theme["grid"], linewidth=0.55, zorder=0)
    ax.set_axisbelow(True)
    start, end = (0.5, 160) if kind == "cost" else (0, 100 if kind == "mistakes" else 60)
    ax.set_xlim(start, end)
    if kind == "cost":
        ax.set_xscale("log")
        ax.set_xticks([1, 5, 10, 25, 50, 100], ["$1", "$5", "$10", "$25", "$50", "$100"])
        ax.xaxis.set_minor_locator(NullLocator())
        axis_label = "Estimated run cost · USD, logarithmic scale"
    else:
        ticks = [0, 25, 50, 75, 100] if kind == "mistakes" else [0, 20, 40, 60]
        ax.set_xticks(ticks, [f"{value}%" for value in ticks])
        axis_label = (
            "Share of all assigned photos" if kind == "mistakes" else "Exact species identified"
        )
    for i, row in enumerate(rows):
        label = f"{row['label']} · {row['effort']}"
        ax.text(
            start,
            i - 0.18,
            label,
            va="center",
            fontsize=12.5 if mobile else 12,
            color=theme["text"],
        )
        if kind == "accuracy":
            ax.barh(
                i + 0.17,
                row["top1"] * 100,
                height=0.2,
                color=theme["colors"][0] if i == 0 else theme["bar"],
                zorder=3,
            )
            value = f"{row['top1']:.2%}"
        elif kind == "cost":
            ax.hlines(i + 0.17, start, row["cost_usd"], color=theme["bar"], linewidth=2)
            ax.scatter(row["cost_usd"], i + 0.17, s=20, color=theme["colors"][0], zorder=3)
            value = f"${row['cost_usd']:.2f}"
        else:
            left = 0
            for key, color in zip(BUCKETS, theme["colors"]):
                amount = row["counts"][key] / row["tasks"] * 100
                ax.barh(i + 0.17, amount, left=left, height=0.23, color=color, zorder=3)
                left += amount
            value = ""
        ax.text(
            end,
            i - 0.18,
            value,
            ha="right",
            va="center",
            color=theme["text"],
            fontsize=12.5 if mobile else 12,
        )
    ax.set_xlabel(axis_label, color=theme["muted"], fontsize=10, labelpad=10)
    if kind == "mistakes":
        labels = ["Exact", "Same genus", "Same family", "Other family", "No valid answer"]
        fig.legend(
            [Patch(color=color) for color in theme["colors"]],
            labels,
            loc="lower center",
            ncol=3 if mobile else 5,
            frameon=False,
            fontsize=10,
            labelcolor=theme["text"],
        )
    return fig


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"svg.hashsalt": "spider-bench-readme", "font.family": "DejaVu Sans"})
    data = collect()
    (OUTPUT / "results.json").write_text(json.dumps(data, indent=2) + "\n")
    for name, theme in THEMES.items():
        for kind in ["accuracy", "cost", "mistakes"]:
            for mobile in [False, True]:
                fig = chart(data, kind, theme, mobile)
                suffix = "-mobile" if mobile else ""
                fig.savefig(
                    OUTPUT / f"{kind}-{name}{suffix}.svg", transparent=True, metadata={"Date": None}
                )
                plt.close(fig)
    print(f"Rendered all {len(data['runs'])} runs across {len(NAMES)} models.")


if __name__ == "__main__":
    main()
