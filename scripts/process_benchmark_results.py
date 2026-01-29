import json
import os
import sys


def main():
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)

    history_file = os.path.join(output_dir, "profiling_history.json")
    current_stats_file = os.path.join(output_dir, "top_20_stats.json")

    if not os.path.exists(current_stats_file):
        print(f"Error: {current_stats_file} not found")
        sys.exit(1)

    with open(current_stats_file, "r", encoding="utf-8") as f:
        current_stats = json.load(f)

    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load history: {e}")

    commit_sha = os.environ.get("GITHUB_SHA", "local")

    # Calculate totals from flat list
    total_duration = sum(s.get("duration", 0) for s in current_stats)
    total_calls = sum(s.get("calls", 0) for s in current_stats)

    # Avoid duplicate entries for the same commit
    history = [h for h in history if h.get("commit") != commit_sha]

    # Create entry with metadata fields for dashboard compatibility
    new_entry = {
        "commit": commit_sha,
        "commit_short": commit_sha[:7] if commit_sha != "local" else "local",
        "total_duration": round(total_duration, 4),
        "total_calls": total_calls,
        "stats": current_stats,
    }
    history.append(new_entry)

    # Limit history
    history = history[-20:]

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    # Generate Markdown for PR comment
    comment_file = os.path.join(output_dir, "profiling_comment.md")
    with open(comment_file, "w", encoding="utf-8") as f:
        f.write("### 🚀 Profiling Results (Top 20 most expensive calls)\n\n")
        f.write("| Function | Calls | Duration (s) |\n")
        f.write("| :--- | :---: | :---: |\n")
        for s in current_stats:
            # Escape pipes in function names if any
            func_name = s["function"].replace("|", "\\|")
            f.write(f"| `{func_name}` | {s['calls']} | {s['duration']:.4f} |\n")

        if len(history) > 1:
            f.write("\n### 📈 Performance Impact per Commit\n\n")
            f.write("| Commit | Total Top-20 Duration (s) | Change |\n")
            f.write("| :--- | :---: | :---: |\n")

            prev_total = None
            for entry in history:
                total_duration = sum(s["duration"] for s in entry["stats"])
                change = "-"
                if prev_total is not None:
                    diff = total_duration - prev_total
                    percent = (diff / prev_total) * 100 if prev_total > 0 else 0
                    color = "🔴" if percent > 5 else "🟢" if percent < -5 else "⚪"
                    change = f"{color} {diff:+.4f} ({percent:+.2f}%)"

                sha_short = entry["commit"][:7]
                f.write(f"| {sha_short} | {total_duration:.4f} | {change} |\n")
                prev_total = total_duration

    print(f"Generated {comment_file}")


if __name__ == "__main__":
    main()
