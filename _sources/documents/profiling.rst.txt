Profiling Dashboard
===================

.. raw:: html

    <link rel="stylesheet" href="../_static/profiling-app/profiling.css">
    
    <div class="profiling-dashboard">
        <div class="dashboard-header">
            <h2>📊 Profiling Dashboard</h2>
            <button id="refresh-btn" class="refresh-btn">🔄 Refresh</button>
        </div>
        
        <div class="tabs">
            <button id="tab-history" class="tab-btn active">📈 History</button>
            <button id="tab-prs" class="tab-btn">🔀 Open PRs</button>
        </div>
        
        <div id="history-panel" class="tab-panel">
            <div id="history-content">
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Loading profiling history...</p>
                </div>
            </div>
        </div>
        
        <div id="prs-panel" class="tab-panel hidden">
            <div id="prs-content">
                <div class="prs-layout" id="prs-layout">
                    <div class="pr-list-container">
                        <div class="pr-list-header">
                            <span class="pr-list-header-text">Open Pull Requests</span>
                            <button class="sidebar-toggle-btn" id="sidebar-toggle" title="Toggle PR list">
                                <span class="toggle-icon">◀</span>
                            </button>
                        </div>
                        <div id="pr-list">
                            <div class="loading">
                                <div class="spinner"></div>
                                <p>Loading PRs...</p>
                            </div>
                        </div>
                    </div>
                    <div class="pr-details-container" id="pr-details">
                        <div class="empty-state">
                            <p>👈 Select a PR to view its profiling data</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script src="../_static/profiling-app/profiling.js"></script>

How it Works
------------

**Main Branch Profiling (Historical Data)**

When a PR is merged to the ``main`` branch, the profiling workflow automatically:

1. Runs the profiling tests on the updated code
2. Collects performance metrics (function calls, durations, etc.)
3. Stores the results in the ``profiling_data`` branch for long-term storage
4. The dashboard above fetches this historical data to show performance trends

**PR Profiling (Open PRs)**

For open pull requests, the PR profiling workflow:

1. Runs profiling tests on the PR branch
2. Uploads artifacts with the profiling results
3. The dashboard can show the status and artifacts for each open PR

**Profiling Triggers**

The profiling workflows are triggered when changes are made to:

- ``src/**`` - Source code changes
- ``tests/profiling/**`` - Profiling test changes  
- ``pyproject.toml`` - Project configuration
- ``pixi.lock`` - Dependency lock file

Data Storage
------------

Profiling data is stored in the ``profiling_data`` branch of this repository. This provides:

- **Persistent storage**: Data survives across workflow runs
- **Version control**: Full history of profiling data changes
- **Easy access**: Data can be fetched via GitHub's raw content API

The history is limited to the last 100 merged commits to prevent unbounded growth.

Interpreting Results
--------------------

**Performance Change Indicators:**

- 🟢 Green: Performance improved by more than 5%
- ⚪ White: Performance change within ±5% (stable)
- 🔴 Red: Performance regressed by more than 5%

**Metrics Tracked:**

- **Total Duration**: Sum of all profiled function durations
- **Total Calls**: Number of function calls tracked
- **Per-test breakdown**: Detailed timing for each profiling test
