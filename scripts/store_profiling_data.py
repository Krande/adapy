"""
Script to store profiling data to a dedicated branch for long-term storage.

This script:
1. Reads the current profiling results from output/top_20_stats.json
2. Fetches existing profiling history from the profiling_data branch
3. Appends the new results with commit metadata
4. Pushes the updated history back to the profiling_data branch
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def run_git(*args, check=True, capture_output=True):
    """Run a git command and return the result."""
    result = subprocess.run(
        ["git"] + list(args),
        check=check,
        capture_output=capture_output,
        text=True,
    )
    return result


def get_commit_info(sha):
    """Get commit information."""
    result = run_git("log", "-1", "--format=%H|%s|%an|%aI", sha)
    parts = result.stdout.strip().split("|", 3)
    return {
        "sha": parts[0],
        "message": parts[1] if len(parts) > 1 else "",
        "author": parts[2] if len(parts) > 2 else "",
        "date": parts[3] if len(parts) > 3 else datetime.now(timezone.utc).isoformat(),
    }


def main():
    output_dir = "output"
    current_stats_file = os.path.join(output_dir, "top_20_stats.json")
    profiling_branch = "profiling_data"
    history_file = "profiling_history.json"

    # Check if current stats exist
    if not os.path.exists(current_stats_file):
        print(f"Error: {current_stats_file} not found")
        sys.exit(1)

    # Read current stats
    with open(current_stats_file, "r", encoding="utf-8") as f:
        current_stats = json.load(f)

    # Get commit info
    commit_sha = os.environ.get("GITHUB_SHA", "local")
    commit_info = get_commit_info(commit_sha) if commit_sha != "local" else {
        "sha": "local",
        "message": "Local run",
        "author": "local",
        "date": datetime.now(timezone.utc).isoformat(),
    }

    # Calculate total duration for quick reference
    total_duration = 0
    total_calls = 0
    
    # Handle both flat list format (from benchmark_profile.py) and nested dict format
    if isinstance(current_stats, list):
        # Flat list: [{"function": ..., "calls": ..., "duration": ...}, ...]
        for func in current_stats:
            total_duration += func.get("duration", 0)
            total_calls += func.get("calls", 0)
    else:
        # Nested dict: {"test_name": [{"function": ..., ...}, ...], ...}
        for test_name, functions in current_stats.items():
            for func in functions:
                total_duration += func.get("duration", 0)
                total_calls += func.get("calls", 0)

    # Create the new entry
    new_entry = {
        "commit": commit_info["sha"],
        "commit_short": commit_info["sha"][:7],
        "message": commit_info["message"],
        "author": commit_info["author"],
        "date": commit_info["date"],
        "total_duration": round(total_duration, 4),
        "total_calls": total_calls,
        "stats": current_stats,
    }

    # Store original branch/commit
    original_ref = run_git("rev-parse", "HEAD").stdout.strip()

    # Try to fetch existing history from profiling_data branch
    history = []
    try:
        # Fetch the branch
        run_git("fetch", "origin", f"{profiling_branch}:{profiling_branch}", check=False)
        
        # Try to read existing history
        result = run_git("show", f"{profiling_branch}:{history_file}", check=False)
        if result.returncode == 0 and result.stdout:
            try:
                history = json.loads(result.stdout)
            except json.JSONDecodeError:
                history = []
    except Exception as e:
        print(f"Note: Could not fetch existing history: {e}")

    # Remove duplicate entry for same commit if exists
    history = [h for h in history if h.get("commit") != commit_info["sha"]]

    # Append new entry
    history.append(new_entry)

    # Keep last 100 entries to prevent unbounded growth
    # This keeps roughly 100 merges worth of data
    history = history[-100:]

    # Write updated history to a temp file
    temp_history_file = os.path.join(output_dir, history_file)
    with open(temp_history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    # Now push to profiling_data branch
    try:
        # Check if profiling_data branch exists remotely
        result = run_git("ls-remote", "--heads", "origin", profiling_branch)
        branch_exists = profiling_branch in result.stdout

        if branch_exists:
            # Checkout the existing branch
            run_git("checkout", profiling_branch)
        else:
            # Create orphan branch (no history from main)
            run_git("checkout", "--orphan", profiling_branch)
            run_git("rm", "-rf", ".", check=False)
            
            # Create a README for the branch
            with open("README.md", "w", encoding="utf-8") as f:
                f.write("# Profiling Data\n\n")
                f.write("This branch contains profiling history data for the adapy project.\n\n")
                f.write("The data is automatically updated when PRs are merged to main.\n\n")
                f.write("## Files\n\n")
                f.write("- `profiling_history.json` - Historical profiling data\n")

        # Copy the history file
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

        # Stage and commit
        run_git("add", history_file)
        if not branch_exists:
            run_git("add", "README.md")

        # Check if there are changes to commit
        result = run_git("diff", "--cached", "--quiet", check=False)
        if result.returncode != 0:
            # Configure git user for GitHub Actions
            run_git("config", "user.name", "github-actions[bot]")
            run_git("config", "user.email", "github-actions[bot]@users.noreply.github.com")

            run_git(
                "commit",
                "-m",
                f"Update profiling data for {commit_info['sha'][:7]}",
            )

            # Push to remote
            run_git("push", "origin", profiling_branch)
            print(f"Successfully pushed profiling data to {profiling_branch} branch")
        else:
            print("No changes to commit")

    except Exception as e:
        print(f"Error pushing to {profiling_branch}: {e}")
        raise
    finally:
        # Return to original ref
        run_git("checkout", original_ref, check=False)

    # Also generate the markdown comment for reference
    generate_markdown_comment(output_dir, current_stats, history)
    print(f"Profiling data stored successfully")


def generate_markdown_comment(output_dir, current_stats, history):
    """Generate a markdown summary of the profiling results."""
    comment_file = os.path.join(output_dir, "profiling_comment.md")
    
    with open(comment_file, "w", encoding="utf-8") as f:
        f.write("### 🚀 Profiling Results (Top 20 Functions)\n\n")
        
        # Handle both flat list and nested dict formats
        if isinstance(current_stats, list):
            # Flat list format from benchmark_profile.py
            f.write("| Function | Calls | Duration (s) |\n")
            f.write("| :--- | :---: | :---: |\n")
            for func in current_stats:
                func_name = func["function"].replace("|", "\\|")
                f.write(f"| `{func_name}` | {func['calls']} | {func['duration']:.4f} |\n")
            f.write("\n")
        else:
            # Nested dict format: {"test_name": [...], ...}
            for test_name, functions in current_stats.items():
                f.write(f"#### 📊 `{test_name}`\n\n")
                f.write("| Function | Calls | Duration (s) |\n")
                f.write("| :--- | :---: | :---: |\n")
                for func in functions:
                    func_name = func["function"].replace("|", "\\|")
                    f.write(f"| `{func_name}` | {func['calls']} | {func['duration']:.4f} |\n")
                f.write("\n")

        if len(history) > 1:
            f.write("### 📈 Recent Performance History\n\n")
            f.write("| Commit | Date | Total Duration (s) | Change |\n")
            f.write("| :--- | :--- | :---: | :---: |\n")
            
            prev_total = None
            for entry in history[-10:]:  # Show last 10 entries
                total_duration = entry.get("total_duration", 0)
                change = "-"
                if prev_total is not None and prev_total > 0:
                    diff = total_duration - prev_total
                    percent = (diff / prev_total) * 100
                    color = "🔴" if percent > 5 else "🟢" if percent < -5 else "⚪"
                    change = f"{color} {diff:+.4f} ({percent:+.2f}%)"
                
                sha_short = entry.get("commit_short", entry.get("commit", "")[:7])
                date = entry.get("date", "")[:10]
                f.write(f"| {sha_short} | {date} | {total_duration:.4f} | {change} |\n")
                prev_total = total_duration

    print(f"Generated {comment_file}")


if __name__ == "__main__":
    main()
