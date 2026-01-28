import subprocess
import sys
import os
import pstats
import json
import datetime

def run_command(cmd, env=None):
    print(f"Running: {' '.join(cmd)}")
    current_env = os.environ.copy()
    if env:
        current_env.update(env)
    
    result = subprocess.run(cmd, capture_output=True, text=True, env=current_env)
    if result.returncode != 0:
        print(f"Command failed with return code {result.returncode}")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
    return result

def process_pstats(prof_file, output_json, top_n=20):
    if not os.path.exists(prof_file):
        print(f"Error: {prof_file} not found.")
        return None
    
    stats = pstats.Stats(prof_file)
    stats.sort_stats("cumulative")
    
    top_stats = []
    
    # Define filters to exclude pytest internals and other profiling overhead
    exclude_patterns = [
        "pytest",
        "pluggy",
        "_pytest",
        "pyinstrument",
        "cProfile.py",
        "importlib",
        "<frozen",
        "scripts/benchmark_profile.py",
        "scripts\\benchmark_profile.py"
    ]
    
    count = 0
    for func_key in stats.fcn_list:
        if count >= top_n:
            break
            
        filename, line, func_name = func_key
        
        # Filtering logic
        if any(p in filename for p in exclude_patterns):
            continue
            
        cc, nc, tt, ct, callers = stats.stats[func_key]
        
        # Clean up filename: remove the absolute path prefix if possible to make it more readable
        # But keep enough to identify it's in src or a dependency
        display_filename = filename
        project_root = os.getcwd()
        if filename.startswith(project_root):
            display_filename = os.path.relpath(filename, project_root)

        top_stats.append({
            "function": f"{func_name} ({display_filename}:{line})",
            "calls": nc,
            "duration": ct
        })
        count += 1
    
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(top_stats, f, indent=2)
    
    return top_stats

def main():
    # Ensure we are in the project root
    project_root = os.environ.get("PIXI_PROJECT_ROOT", os.getcwd())
    os.chdir(project_root)

    # Output directory
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)

    # Output files
    html_report = os.path.join(output_dir, "profiling_report.html")
    prof_data = os.path.join(output_dir, "profiling_data.prof")
    stats_json = os.path.join(output_dir, "top_20_stats.json")

    # 1. Run pyinstrument to get HTML
    # We use -m benchmark to filter tests
    print("Running pyinstrument...")
    run_command([
        "pyinstrument", 
        "-r", "html", 
        "-o", html_report, 
        "-m", "pytest", 
        "-m", "benchmark", 
        "tests/profiling"
    ])
    
    # 2. Run cProfile to get raw data and call counts
    print("Running cProfile...")
    run_command([
        "python", "-m", "cProfile", 
        "-o", prof_data, 
        "-m", "pytest", 
        "-m", "benchmark", 
        "tests/profiling"
    ])
    
    # 3. Process cProfile data to get top 20
    print("Processing stats...")
    top_20 = process_pstats(prof_data, stats_json)
    
    if top_20:
        print(f"Profiling completed successfully.")
        print(f"HTML report: {html_report}")
        print(f"Raw data: {prof_data}")
        print(f"Top 20 stats: {stats_json}")
    else:
        print("Profiling failed to generate stats.")
        sys.exit(1)

if __name__ == "__main__":
    main()
