/**
 * Profiling Dashboard SPA
 * 
 * Fetches and displays profiling data from:
 * 1. profiling_data branch (historical data from merged PRs)
 * 2. GitHub API for open PR artifacts
 */

const CONFIG = {
    owner: 'Krande',
    repo: 'adapy',
    profilingBranch: 'profiling_data',
    historyFile: 'profiling_history.json',
};

// State
let state = {
    history: [],
    openPRs: [],
    selectedPR: null,
    prProfilingData: null,
    activeTab: 'history',
    loading: {
        history: false,
        prs: false,
        prData: false,
    },
    errors: {
        history: null,
        prs: null,
        prData: null,
    },
};

// DOM Elements
const elements = {};

// Initialize the app
document.addEventListener('DOMContentLoaded', () => {
    cacheElements();
    setupEventListeners();
    loadInitialData();
});

function cacheElements() {
    elements.tabHistory = document.getElementById('tab-history');
    elements.tabPRs = document.getElementById('tab-prs');
    elements.historyPanel = document.getElementById('history-panel');
    elements.prsPanel = document.getElementById('prs-panel');
    elements.historyContent = document.getElementById('history-content');
    elements.prsContent = document.getElementById('prs-content');
    elements.prDetails = document.getElementById('pr-details');
    elements.prList = document.getElementById('pr-list');
    elements.refreshBtn = document.getElementById('refresh-btn');
    elements.prsLayout = document.getElementById('prs-layout');
    elements.sidebarToggle = document.getElementById('sidebar-toggle');
}

function setupEventListeners() {
    elements.tabHistory.addEventListener('click', () => switchTab('history'));
    elements.tabPRs.addEventListener('click', () => switchTab('prs'));
    elements.refreshBtn.addEventListener('click', loadInitialData);
    elements.sidebarToggle?.addEventListener('click', toggleSidebar);
    
    // Restore sidebar state from localStorage
    restoreSidebarState();
}

function toggleSidebar() {
    if (!elements.prsLayout) return;
    
    const isCollapsed = elements.prsLayout.classList.toggle('sidebar-collapsed');
    
    // Persist state to localStorage
    try {
        localStorage.setItem('profiling-sidebar-collapsed', isCollapsed ? 'true' : 'false');
    } catch (e) {
        // localStorage may not be available
    }
}

function restoreSidebarState() {
    if (!elements.prsLayout) return;
    
    try {
        const isCollapsed = localStorage.getItem('profiling-sidebar-collapsed') === 'true';
        if (isCollapsed) {
            elements.prsLayout.classList.add('sidebar-collapsed');
        }
    } catch (e) {
        // localStorage may not be available
    }
}

function switchTab(tab) {
    state.activeTab = tab;
    
    // Update tab buttons
    elements.tabHistory.classList.toggle('active', tab === 'history');
    elements.tabPRs.classList.toggle('active', tab === 'prs');
    
    // Update panels
    elements.historyPanel.classList.toggle('hidden', tab !== 'history');
    elements.prsPanel.classList.toggle('hidden', tab !== 'prs');
}

async function loadInitialData() {
    await Promise.all([
        loadProfilingHistory(),
        loadOpenPRs(),
    ]);
}

async function loadProfilingHistory() {
    state.loading.history = true;
    state.errors.history = null;
    renderHistoryLoading();
    
    try {
        const url = `https://raw.githubusercontent.com/${CONFIG.owner}/${CONFIG.repo}/${CONFIG.profilingBranch}/${CONFIG.historyFile}`;
        const response = await fetch(url);
        
        if (!response.ok) {
            if (response.status === 404) {
                state.history = [];
                state.errors.history = 'No profiling history found. The profiling_data branch may not exist yet.';
            } else {
                throw new Error(`HTTP ${response.status}`);
            }
        } else {
            state.history = await response.json();
        }
    } catch (error) {
        state.errors.history = `Failed to load profiling history: ${error.message}`;
        state.history = [];
    }
    
    state.loading.history = false;
    renderHistory();
}

async function loadOpenPRs() {
    state.loading.prs = true;
    state.errors.prs = null;
    renderPRsLoading();
    
    try {
        const url = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/pulls?state=open&per_page=20`;
        
        // Add timeout to prevent hanging
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10000); // 10 second timeout
        
        const response = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        state.openPRs = await response.json();
    } catch (error) {
        if (error.name === 'AbortError') {
            state.errors.prs = 'Request timed out. GitHub API may be slow or unavailable.';
        } else {
            state.errors.prs = `Failed to load open PRs: ${error.message}`;
        }
        state.openPRs = [];
    }
    
    state.loading.prs = false;
    renderPRsList();
}

async function loadPRProfilingData(pr) {
    state.selectedPR = pr;
    state.loading.prData = true;
    state.errors.prData = null;
    state.prProfilingData = null;
    state.showPRInfo = false; // Reset info panel state
    renderPRDetails();
    
    try {
        // Fetch PR comments to get profiling results
        const commentsUrl = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/issues/${pr.number}/comments?per_page=50`;
        const commentsResponse = await fetch(commentsUrl);
        
        let profilingComment = null;
        if (commentsResponse.ok) {
            const comments = await commentsResponse.json();
            // Find the profiling results comment (posted by the workflow)
            profilingComment = comments.find(c => 
                c.body?.includes('Profiling Results') || 
                c.body?.includes('profiling-results') ||
                c.body?.includes('🚀 Profiling Results')
            );
        }
        
        // Also fetch workflow run info for the info panel
        const runsUrl = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/actions/runs?head_sha=${pr.head.sha}&per_page=10`;
        const runsResponse = await fetch(runsUrl);
        
        let profilingRun = null;
        let profilingArtifact = null;
        
        if (runsResponse.ok) {
            const runsData = await runsResponse.json();
            profilingRun = runsData.workflow_runs.find(run => 
                run.name === 'PR Profiling' || run.path?.includes('pr-profiling')
            );
            
            if (profilingRun) {
                // Get artifacts for this run
                const artifactsUrl = `https://api.github.com/repos/${CONFIG.owner}/${CONFIG.repo}/actions/runs/${profilingRun.id}/artifacts`;
                const artifactsResponse = await fetch(artifactsUrl);
                
                if (artifactsResponse.ok) {
                    const artifactsData = await artifactsResponse.json();
                    profilingArtifact = artifactsData.artifacts.find(a => 
                        a.name.startsWith('profiling-results')
                    );
                }
            }
        }
        
        if (!profilingComment && !profilingRun) {
            state.errors.prData = 'No profiling data found for this PR. The workflow may not have run yet.';
            state.loading.prData = false;
            renderPRDetails();
            return;
        }
        
        // Parse profiling results from comment if available
        let parsedResults = null;
        if (profilingComment) {
            parsedResults = parseProfilingComment(profilingComment.body);
        }
        
        state.prProfilingData = {
            run: profilingRun,
            artifact: profilingArtifact,
            comment: profilingComment,
            results: parsedResults,
        };
        
    } catch (error) {
        state.errors.prData = `Failed to load PR profiling data: ${error.message}`;
    }
    
    state.loading.prData = false;
    renderPRDetails();
}

// Parse the profiling markdown comment into structured data
function parseProfilingComment(body) {
    const results = {
        tests: {},
        history: [],
    };
    
    if (!body) return results;
    
    const lines = body.split('\n');
    let currentTest = null;
    let inTable = false;
    let inHistoryTable = false;
    
    for (const line of lines) {
        // Detect test section headers (e.g., "#### 📊 `test_build_big_ifc_beams`")
        const testMatch = line.match(/#{3,4}\s*📊?\s*`?([^`\n]+)`?/);
        if (testMatch) {
            currentTest = testMatch[1].trim();
            results.tests[currentTest] = [];
            inTable = false;
            inHistoryTable = false;
            continue;
        }
        
        // Detect history section
        if (line.includes('Performance Impact') || line.includes('📈')) {
            currentTest = null;
            inHistoryTable = true;
            inTable = false;
            continue;
        }
        
        // Skip table headers
        if (line.includes('| :---') || line.includes('|:---')) {
            inTable = true;
            continue;
        }
        
        // Parse table rows
        if (line.startsWith('|') && inTable) {
            const cells = line.split('|').map(c => c.trim()).filter(c => c);
            
            if (inHistoryTable && cells.length >= 3) {
                // History table: Commit | Duration | Change
                results.history.push({
                    commit: cells[0],
                    duration: parseFloat(cells[1]) || 0,
                    change: cells[2] || '-',
                });
            } else if (currentTest && cells.length >= 3) {
                // Function table: Function | Calls | Duration
                const funcName = cells[0].replace(/`/g, '');
                const calls = parseInt(cells[1].replace(/,/g, '')) || 0;
                const duration = parseFloat(cells[2]) || 0;
                
                results.tests[currentTest].push({
                    function: funcName,
                    calls: calls,
                    duration: duration,
                });
            }
        }
        
        // Reset table state on empty lines
        if (line.trim() === '') {
            inTable = false;
        }
    }
    
    return results;
}

function togglePRInfo() {
    state.showPRInfo = !state.showPRInfo;
    renderPRDetails();
}

// Helper function to calculate total duration from stats
function calculateStatsTotal(stats) {
    if (!stats) return { duration: 0, calls: 0 };
    
    let totalDuration = 0;
    let totalCalls = 0;
    
    if (Array.isArray(stats)) {
        // Flat list format
        for (const f of stats) {
            totalDuration += f.duration || 0;
            totalCalls += f.calls || 0;
        }
    } else if (typeof stats === 'object') {
        // Nested dict format
        for (const functions of Object.values(stats)) {
            if (Array.isArray(functions)) {
                for (const f of functions) {
                    totalDuration += f.duration || 0;
                    totalCalls += f.calls || 0;
                }
            }
        }
    }
    
    return { duration: totalDuration, calls: totalCalls };
}

// Rendering functions
function renderHistoryLoading() {
    elements.historyContent.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>Loading profiling history...</p>
        </div>
    `;
}

function renderHistory() {
    if (state.errors.history && state.history.length === 0) {
        elements.historyContent.innerHTML = `
            <div class="error-message">
                <p>⚠️ ${state.errors.history}</p>
                <p class="hint">Profiling data will be available after the first PR is merged to main with the profiling workflow enabled.</p>
            </div>
        `;
        return;
    }
    
    if (state.history.length === 0) {
        elements.historyContent.innerHTML = `
            <div class="empty-state">
                <p>📊 No profiling data available yet.</p>
            </div>
        `;
        return;
    }
    
    // Calculate stats
    const latest = state.history[state.history.length - 1];
    const stats = latest.stats || [];
    
    // Handle both flat list and nested dict formats
    let totalDuration = latest.total_duration || 0;
    let totalCalls = latest.total_calls || 0;
    
    // Calculate from stats if totals are missing
    if (!totalDuration || !totalCalls) {
        const calculated = calculateStatsTotal(stats);
        if (!totalDuration) totalDuration = calculated.duration;
        if (!totalCalls) totalCalls = calculated.calls;
    }
    
    // Determine format and item count
    const isFlat = Array.isArray(stats);
    const itemCount = isFlat ? stats.length : Object.keys(stats).length;
    
    // Build chart data - calculate duration from stats if total_duration missing
    const chartData = state.history.slice(-20).map(entry => {
        let duration = entry.total_duration;
        if (duration === undefined || duration === null || duration === 0) {
            const totals = calculateStatsTotal(entry.stats);
            duration = totals.duration;
        }
        return {
            commit: entry.commit_short || entry.commit?.substring(0, 7) || '?',
            duration: duration,
            date: entry.date?.substring(0, 10) || '',
        };
    });
    
    const maxDuration = Math.max(...chartData.map(d => d.duration), 1);
    
    let html = `
        <div class="stats-summary">
            <div class="stat-card">
                <div class="stat-value">${totalDuration.toFixed(2)}s</div>
                <div class="stat-label">Total Duration</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${itemCount}</div>
                <div class="stat-label">${isFlat ? 'Functions Tracked' : 'Tests Profiled'}</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${totalCalls.toLocaleString()}</div>
                <div class="stat-label">Total Calls</div>
            </div>
        </div>
        
        <h3>📈 Performance Over Time</h3>
        <div class="chart-container">
            <div class="bar-chart">
                ${chartData.map((d, i) => `
                    <div class="bar-wrapper" title="${d.commit}: ${d.duration.toFixed(2)}s">
                        <div class="bar" style="height: ${(d.duration / maxDuration) * 100}%"></div>
                        <div class="bar-label">${d.commit}</div>
                    </div>
                `).join('')}
            </div>
        </div>
        
        <h3>📋 History Table</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th>Commit</th>
                    <th>Date</th>
                    <th>Author</th>
                    <th>Duration</th>
                    <th>Change</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    // Reverse to show newest first
    const reversedHistory = [...state.history].reverse();
    let prevDuration = null;
    
    for (let i = reversedHistory.length - 1; i >= 0; i--) {
        const entry = reversedHistory[i];
        // Calculate duration from stats if total_duration is missing
        let duration = entry.total_duration;
        if (duration === undefined || duration === null || duration === 0) {
            const totals = calculateStatsTotal(entry.stats);
            duration = totals.duration;
        }
        entry._calculatedDuration = duration;
        
        let changeHtml = '-';
        
        if (prevDuration !== null && prevDuration > 0) {
            const diff = duration - prevDuration;
            const percent = (diff / prevDuration) * 100;
            const icon = percent > 5 ? '🔴' : percent < -5 ? '🟢' : '⚪';
            changeHtml = `${icon} ${diff >= 0 ? '+' : ''}${diff.toFixed(2)}s (${percent >= 0 ? '+' : ''}${percent.toFixed(1)}%)`;
        }
        prevDuration = duration;
        
        // Store for next iteration (going forward in time)
        reversedHistory[i]._change = changeHtml;
    }
    
    for (const entry of reversedHistory) {
        const commitShort = entry.commit_short || entry.commit?.substring(0, 7) || '?';
        const date = entry.date?.substring(0, 10) || '';
        const author = entry.author || '';
        const duration = entry._calculatedDuration || 0;
        
        html += `
            <tr>
                <td><code>${commitShort}</code></td>
                <td>${date}</td>
                <td>${author}</td>
                <td>${duration.toFixed(2)}s</td>
                <td>${entry._change}</td>
            </tr>
        `;
    }
    
    html += `
            </tbody>
        </table>
        
        <h3>🔍 Latest Profiling Results</h3>
    `;
    
    // Check if stats is a flat list (from benchmark_profile.py) or nested dict (by test name)
    if (Array.isArray(stats)) {
        // Flat list format: [{function, calls, duration}, ...]
        html += `
            <div class="test-section expanded">
                <div class="test-header" onclick="this.parentElement.classList.toggle('expanded')">
                    <span class="expand-icon">▶</span>
                    <span class="test-name">Top 20 Functions</span>
                    <span class="test-duration">${totalDuration.toFixed(2)}s</span>
                </div>
                <div class="test-details">
                    <table class="data-table compact">
                        <thead>
                            <tr>
                                <th>Function</th>
                                <th>Calls</th>
                                <th>Duration</th>
                            </tr>
                        </thead>
                        <tbody>
        `;
        
        for (const func of stats) {
            html += `
                <tr>
                    <td><code>${func.function || ''}</code></td>
                    <td>${(func.calls || 0).toLocaleString()}</td>
                    <td>${(func.duration || 0).toFixed(4)}s</td>
                </tr>
            `;
        }
        
        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    } else {
        // Nested dict format: {testName: [{function, calls, duration}, ...], ...}
        for (const [testName, functions] of Object.entries(stats)) {
            if (!Array.isArray(functions)) continue; // Skip invalid entries
            
            const testDuration = functions.reduce((sum, f) => sum + (f.duration || 0), 0);
            
            html += `
                <div class="test-section">
                    <div class="test-header" onclick="this.parentElement.classList.toggle('expanded')">
                        <span class="expand-icon">▶</span>
                        <span class="test-name">${testName}</span>
                        <span class="test-duration">${testDuration.toFixed(2)}s</span>
                    </div>
                    <div class="test-details">
                        <table class="data-table compact">
                            <thead>
                                <tr>
                                    <th>Function</th>
                                    <th>Calls</th>
                                    <th>Duration</th>
                                </tr>
                            </thead>
                            <tbody>
            `;
            
            for (const func of functions) {
                html += `
                    <tr>
                        <td><code>${func.function || ''}</code></td>
                        <td>${(func.calls || 0).toLocaleString()}</td>
                        <td>${(func.duration || 0).toFixed(4)}s</td>
                    </tr>
                `;
            }
            
            html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }
    }
    
    elements.historyContent.innerHTML = html;
}

function renderPRsLoading() {
    elements.prList.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>Loading open PRs...</p>
        </div>
    `;
}

function renderPRsList() {
    if (state.errors.prs) {
        elements.prList.innerHTML = `
            <div class="error-message">
                <p>⚠️ ${state.errors.prs}</p>
            </div>
        `;
        return;
    }
    
    if (state.openPRs.length === 0) {
        elements.prList.innerHTML = `
            <div class="empty-state">
                <p>🎉 No open pull requests.</p>
            </div>
        `;
        return;
    }
    
    let html = '<div class="pr-list-items">';
    
    for (const pr of state.openPRs) {
        const isSelected = state.selectedPR?.id === pr.id;
        html += `
            <div class="pr-item ${isSelected ? 'selected' : ''}" onclick="loadPRProfilingData(window.adapyPRs[${pr.id}])">
                <div class="pr-number">#${pr.number}</div>
                <div class="pr-info">
                    <div class="pr-title">${escapeHtml(pr.title)}</div>
                    <div class="pr-meta">
                        <span class="pr-author">by ${pr.user?.login || 'unknown'}</span>
                        <span class="pr-date">${new Date(pr.created_at).toLocaleDateString()}</span>
                    </div>
                </div>
            </div>
        `;
    }
    
    html += '</div>';
    elements.prList.innerHTML = html;
    
    // Store PRs in window for onclick access
    window.adapyPRs = {};
    for (const pr of state.openPRs) {
        window.adapyPRs[pr.id] = pr;
    }
}

function renderPRDetails() {
    if (!state.selectedPR) {
        elements.prDetails.innerHTML = `
            <div class="empty-state">
                <p>👈 Select a PR to view its profiling data</p>
            </div>
        `;
        return;
    }
    
    const pr = state.selectedPR;
    
    let html = `
        <div class="pr-details-header">
            <div class="pr-title-row">
                <h3>
                    <a href="${pr.html_url}" target="_blank" rel="noopener">
                        #${pr.number}: ${escapeHtml(pr.title)}
                    </a>
                </h3>
                ${state.prProfilingData ? `
                    <button class="info-toggle-btn ${state.showPRInfo ? 'active' : ''}" onclick="togglePRInfo()" title="Toggle workflow info">
                        ℹ️
                    </button>
                ` : ''}
            </div>
            <p class="pr-meta">
                Branch: <code>${pr.head?.ref || 'unknown'}</code> • 
                SHA: <code>${pr.head?.sha?.substring(0, 7) || 'unknown'}</code>
            </p>
        </div>
    `;
    
    if (state.loading.prData) {
        html += `
            <div class="loading">
                <div class="spinner"></div>
                <p>Loading profiling data...</p>
            </div>
        `;
    } else if (state.errors.prData) {
        html += `
            <div class="error-message">
                <p>⚠️ ${state.errors.prData}</p>
            </div>
        `;
    } else if (state.prProfilingData) {
        const { run, artifact, results } = state.prProfilingData;
        
        // Show info panel if toggled
        if (state.showPRInfo && run) {
            html += `
                <div class="pr-info-panel">
                    <h4>🚀 Workflow Run Info</h4>
                    <table class="info-table">
                        <tr>
                            <th>Status</th>
                            <td>
                                <span class="status-badge status-${run.conclusion || run.status}">
                                    ${run.conclusion || run.status}
                                </span>
                            </td>
                        </tr>
                        <tr>
                            <th>Run ID</th>
                            <td><a href="${run.html_url}" target="_blank">${run.id}</a></td>
                        </tr>
                        <tr>
                            <th>Started</th>
                            <td>${new Date(run.created_at).toLocaleString()}</td>
                        </tr>
                        <tr>
                            <th>Updated</th>
                            <td>${new Date(run.updated_at).toLocaleString()}</td>
                        </tr>
                    </table>
                    ${artifact ? `
                        <h4>📦 Artifact</h4>
                        <table class="info-table">
                            <tr>
                                <th>Name</th>
                                <td><code>${artifact.name}</code></td>
                            </tr>
                            <tr>
                                <th>Size</th>
                                <td>${formatBytes(artifact.size_in_bytes)}</td>
                            </tr>
                            <tr>
                                <th>Expires</th>
                                <td>${new Date(artifact.expires_at).toLocaleString()}</td>
                            </tr>
                        </table>
                    ` : ''}
                </div>
            `;
        }
        
        // Show profiling results (table and graph)
        if (results && Object.keys(results.tests).length > 0) {
            // Calculate totals for summary
            let totalDuration = 0;
            let totalCalls = 0;
            for (const functions of Object.values(results.tests)) {
                for (const f of functions) {
                    totalDuration += f.duration;
                    totalCalls += f.calls;
                }
            }
            
            html += `
                <div class="pr-profiling-results">
                    <div class="stats-summary compact">
                        <div class="stat-card">
                            <div class="stat-value">${totalDuration.toFixed(2)}s</div>
                            <div class="stat-label">Total Duration</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">${Object.keys(results.tests).length}</div>
                            <div class="stat-label">Tests</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">${totalCalls.toLocaleString()}</div>
                            <div class="stat-label">Total Calls</div>
                        </div>
                    </div>
                    
                    <h4>📊 Performance by Test</h4>
                    <div class="pr-chart-container">
                        <div class="bar-chart horizontal">
            `;
            
            // Build horizontal bar chart for tests
            const testDurations = Object.entries(results.tests).map(([name, functions]) => ({
                name,
                duration: functions.reduce((sum, f) => sum + f.duration, 0),
            })).sort((a, b) => b.duration - a.duration);
            
            const maxTestDuration = Math.max(...testDurations.map(t => t.duration), 1);
            
            for (const test of testDurations) {
                const pct = (test.duration / maxTestDuration) * 100;
                html += `
                    <div class="h-bar-row">
                        <div class="h-bar-label" title="${test.name}">${test.name}</div>
                        <div class="h-bar-track">
                            <div class="h-bar" style="width: ${pct}%"></div>
                        </div>
                        <div class="h-bar-value">${test.duration.toFixed(2)}s</div>
                    </div>
                `;
            }
            
            html += `
                        </div>
                    </div>
                    
                    <h4>📋 Detailed Results</h4>
            `;
            
            // Show detailed results for each test
            for (const [testName, functions] of Object.entries(results.tests)) {
                const testDuration = functions.reduce((sum, f) => sum + f.duration, 0);
                
                html += `
                    <div class="test-section">
                        <div class="test-header" onclick="this.parentElement.classList.toggle('expanded')">
                            <span class="expand-icon">▶</span>
                            <span class="test-name">${testName}</span>
                            <span class="test-duration">${testDuration.toFixed(2)}s</span>
                        </div>
                        <div class="test-details">
                            <table class="data-table compact">
                                <thead>
                                    <tr>
                                        <th>Function</th>
                                        <th>Calls</th>
                                        <th>Duration</th>
                                    </tr>
                                </thead>
                                <tbody>
                `;
                
                for (const func of functions) {
                    html += `
                        <tr>
                            <td><code>${escapeHtml(func.function)}</code></td>
                            <td>${func.calls.toLocaleString()}</td>
                            <td>${func.duration.toFixed(4)}s</td>
                        </tr>
                    `;
                }
                
                html += `
                                </tbody>
                            </table>
                        </div>
                    </div>
                `;
            }
            
            html += '</div>';
        } else if (run) {
            // No parsed results, show a message with link to workflow
            html += `
                <div class="no-results-message">
                    <p>📊 Profiling workflow found but no comment with results yet.</p>
                    <p class="hint">
                        The workflow may still be running or the comment hasn't been posted yet.
                        <br>Check the <a href="${run.html_url}" target="_blank">workflow run</a> for details.
                    </p>
                </div>
            `;
        }
    }
    
    elements.prDetails.innerHTML = html;
    
    // Re-render PR list to update selection
    renderPRsList();
}

// Make togglePRInfo available globally
window.togglePRInfo = togglePRInfo;

// Utility functions
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Make loadPRProfilingData available globally for onclick
window.loadPRProfilingData = loadPRProfilingData;
