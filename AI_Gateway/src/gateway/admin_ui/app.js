// Admin UI Application Logic

let adminKey = '';
let timelineChart = null;
let modelChart = null;
let sparklineChart = null;
let liveTimer = null;
let analyticsTimer = null;
let analyticsRange = '24h';
const analyticsCharts = {};
const PALETTE = ['#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#8b5cf6', '#22c55e', '#f97316', '#06b6d4'];

// Chart.js defaults for dark theme
Chart.defaults.color = '#8b92a5';
Chart.defaults.borderColor = 'rgba(255,255,255,0.05)';
Chart.defaults.font.family = "'Outfit', sans-serif";

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initModals();
    initAuth();
    initAnalyticsControls();
});

function initAuth() {
    const btnLogin = document.getElementById('btn-login');
    const inputKey = document.getElementById('admin-key-input');
    const errObj = document.getElementById('login-error');

    btnLogin.addEventListener('click', async () => {
        const key = inputKey.value.trim();
        btnLogin.textContent = 'Connecting...';
        
        try {
            // Test the key by fetching the dashboard stats
            const res = await fetch('/v1/admin/dashboard', {
                headers: { 'Authorization': key ? `Bearer ${key}` : '' }
            });
            
            if (res.ok) {
                adminKey = key;
                document.getElementById('auth-overlay').classList.remove('active');
                
                const data = await res.json();
                updateDashboard(data);
                loadKeys();
                startLivePolling();
            } else {
                errObj.textContent = 'Access Denied. Invalid Admin Key.';
                btnLogin.textContent = 'Connect';
            }
        } catch (e) {
            errObj.textContent = 'Connection error. Is the server running?';
            btnLogin.textContent = 'Connect';
        }
    });
    
    // Allow enter key
    inputKey.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') btnLogin.click();
    });
}

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            e.preventDefault();
            const targetId = item.getAttribute('data-target');
            
            // Update nav active state
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            item.classList.add('active');
            
            // Update views
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.getElementById(targetId).classList.add('active');
            
            // Load data for view
            if (targetId === 'dashboard') {
                const res = await fetch('/v1/admin/dashboard', { headers: authHeaders() });
                if (res.ok) updateDashboard(await res.json());
                startLivePolling();
            } else {
                stopLivePolling();
            }
            if (targetId === 'analytics') {
                loadAnalytics();
                startAnalyticsPolling();
            } else {
                stopAnalyticsPolling();
            }
            if (targetId === 'keys') {
                loadKeys();
            } else if (targetId === 'models') {
                loadModels();
            } else if (targetId === 'routes') {
                loadRoutes();
            } else if (targetId === 'health') {
                loadHealth();
            } else if (targetId === 'cache') {
                loadCache();
            }
        });
    });
}

function initModals() {
    const createModal = document.getElementById('create-key-modal');
    const showModal = document.getElementById('show-key-modal');
    
    document.getElementById('btn-create-key').addEventListener('click', () => {
        createModal.classList.add('active');
    });
    
    document.getElementById('btn-close-modal').addEventListener('click', () => {
        createModal.classList.remove('active');
    });
    
    document.getElementById('btn-cancel-modal').addEventListener('click', () => {
        createModal.classList.remove('active');
    });
    
    document.getElementById('btn-close-show-modal').addEventListener('click', () => {
        showModal.classList.remove('active');
    });
    
    document.getElementById('btn-done-modal').addEventListener('click', () => {
        showModal.classList.remove('active');
    });
    
    document.getElementById('btn-copy-key').addEventListener('click', () => {
        const key = document.getElementById('new-raw-key').textContent;
        navigator.clipboard.writeText(key);
        alert('Copied to clipboard!');
    });

    const addModelModal = document.getElementById('add-model-modal');
    document.getElementById('btn-add-model').addEventListener('click', () => addModelModal.classList.add('active'));
    document.getElementById('btn-close-model-modal').addEventListener('click', () => addModelModal.classList.remove('active'));
    document.getElementById('btn-cancel-model-modal').addEventListener('click', () => addModelModal.classList.remove('active'));
    document.getElementById('btn-test-model').addEventListener('click', testModelConnection);
    document.getElementById('add-model-form').addEventListener('submit', submitAddModel);
    document.getElementById('btn-save-routes').addEventListener('click', saveRoutes);
    document.getElementById('btn-add-webhook').addEventListener('click', addWebhook);
    
    document.getElementById('create-key-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const payload = {
            name: document.getElementById('key-name').value,
            project_id: document.getElementById('key-project').value,
            rate_limits: {
                rpm: parseInt(document.getElementById('key-rpm').value),
                tpm: parseInt(document.getElementById('key-tpm').value)
            },
            permissions: { allowed_models: ["*"], allowed_endpoints: ["*"], tags: [] }
        };
        
        const res = await fetch('/v1/admin/keys', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders()
            },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            const data = await res.json();
            createModal.classList.remove('active');
            document.getElementById('new-raw-key').textContent = data.key;
            showModal.classList.add('active');
            document.getElementById('create-key-form').reset();
            loadKeys();
        } else {
            alert('Failed to create key');
        }
    });
}

function authHeaders() {
    return adminKey ? { 'Authorization': `Bearer ${adminKey}` } : {};
}

// -- Data Loading --

function updateDashboard(data) {
    document.getElementById('val-requests').textContent = data.total_requests.toLocaleString();
    document.getElementById('val-tokens').textContent = data.total_tokens.toLocaleString();
    document.getElementById('val-cost').textContent = `$${data.total_cost_usd.toFixed(4)}`;
    applyLive(data.live || {});
    renderNamedBudgets('named-budget-grid', data.named_agents || {});
    renderTimelineChart(data.timeline);
    renderModelChart(data.model_costs);
}

function renderNamedBudgets(elId, named) {
    const el = document.getElementById(elId);
    if (!el) return;
    const rows = Object.entries(named || {});
    if (!rows.length) {
        el.innerHTML = '';
        return;
    }
    el.innerHTML = rows.map(([name, row]) => `
        <div class="metric-card glass-panel">
            <h3>${name}</h3>
            <div class="value">$${(row.remaining_usd ?? 0).toFixed(2)}</div>
            <p class="subtitle">of $${Number(row.limit_usd || 0).toFixed(0)} · ${row.rpm} rpm · spent $${Number(row.spent_usd || 0).toFixed(4)}</p>
        </div>
    `).join('');
}

async function loadCache() {
    const res = await fetch('/v1/admin/cache', { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const today = data.today || {};
    const set = (id, v) => { const n = document.getElementById(id); if (n) n.textContent = v; };
    set('cache-exact', today.exact_hits ?? '-');
    set('cache-semantic', today.semantic_hits ?? '-');
    set('cache-miss', today.misses ?? '-');
    set('cache-rate', `${Math.round((today.cache_hit_rate || 0) * 100)}%`);
    const store = document.getElementById('cache-store');
    if (store) store.textContent = JSON.stringify(data.store || {}, null, 2);
    renderNamedBudgets('cache-budget-grid', data.named_agents || {});
}

function applyLive(live) {
    live = live || {};
    const tpmEl = document.getElementById('val-tpm');
    const rpmEl = document.getElementById('val-rpm');
    const quotaEl = document.getElementById('val-quota');
    if (tpmEl) tpmEl.textContent = (live.tpm || 0).toLocaleString();
    if (rpmEl) rpmEl.textContent = (live.rpm || 0).toLocaleString();
    let quota = live.quota_pct;
    if (quota == null && Array.isArray(live.models) && live.models.length) {
        quota = Math.max(0, ...live.models.map(m => (m.utilization || 0) * 100));
    }
    if (quotaEl) quotaEl.textContent = `${(quota || 0).toFixed(0)}%`;
    if (live.sparkline) renderSparkline(live.sparkline);
}

function startLivePolling() {
    stopLivePolling();
    liveTimer = setInterval(async () => {
        const res = await fetch('/v1/admin/live', { headers: authHeaders() });
        if (res.ok) applyLive(await res.json());
    }, 5000);
}

function stopLivePolling() {
    if (liveTimer) {
        clearInterval(liveTimer);
        liveTimer = null;
    }
}

async function loadKeys() {
    const res = await fetch('/v1/admin/keys', { headers: authHeaders() });
    if (!res.ok) return;
    
    const keys = await res.json();
    const tbody = document.getElementById('keys-tbody');
    tbody.innerHTML = '';
    
    keys.sort((a,b) => new Date(b.created_at) - new Date(a.created_at)).forEach(key => {
        const tr = document.createElement('tr');
        const d = new Date(key.created_at);
        const statusClass = key.is_active ? 'active' : 'revoked';
        
        tr.innerHTML = `
            <td><strong>${key.name}</strong></td>
            <td><code>${key.key_prefix}...</code></td>
            <td>${key.project_id}</td>
            <td><span class="status-badge ${statusClass}">${key.is_active ? 'Active' : 'Revoked'}</span></td>
            <td>${d.toLocaleDateString()}</td>
            <td>
                ${key.is_active ? `<button class="btn btn-danger btn-icon" onclick="revokeKey('${key.id}')" title="Revoke Key">Revoke</button>` : ''}
            </td>
        `;
        tbody.appendChild(tr);
    });
}

window.revokeKey = async function(id) {
    if (!confirm('Are you sure you want to revoke this key? This cannot be undone.')) return;
    
    const res = await fetch(`/v1/admin/keys/${id}`, {
        method: 'DELETE',
        headers: authHeaders()
    });
    
    if (res.ok) {
        loadKeys();
    } else {
        alert('Failed to revoke key');
    }
}

// -- Charts --

function renderTimelineChart(timelineData) {
    const ctx = document.getElementById('timelineChart').getContext('2d');
    
    // Sort dates
    const dates = Object.keys(timelineData).sort();
    // Fill in last 7 days if empty
    if (dates.length === 0) {
        const today = new Date();
        dates.push(today.toISOString().split('T')[0]);
        timelineData[dates[0]] = {requests: 0, cost: 0};
    }
    
    const dataReq = dates.map(d => timelineData[d].requests);
    const dataCost = dates.map(d => timelineData[d].cost || 0);
    
    if (timelineChart) timelineChart.destroy();
    
    timelineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: dates,
            datasets: [{
                label: 'Requests',
                data: dataReq,
                borderColor: '#6366f1',
                backgroundColor: 'rgba(99, 102, 241, 0.1)',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                yAxisID: 'y',
                pointBackgroundColor: '#191c29',
                pointBorderColor: '#6366f1'
            }, {
                label: 'Spend USD',
                data: dataCost,
                borderColor: '#ec4899',
                backgroundColor: 'rgba(236, 72, 153, 0.08)',
                borderWidth: 2,
                tension: 0.4,
                fill: false,
                yAxisID: 'y1',
                pointRadius: 0,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10 } } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: 'Requests' } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'USD' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function renderModelChart(modelCosts) {
    const ctx = document.getElementById('modelChart').getContext('2d');
    
    const labels = Object.keys(modelCosts);
    const data = Object.values(modelCosts);
    
    if (labels.length === 0) {
        labels.push("None");
        data.push(1); // placeholder for empty ring
    }
    
    if (modelChart) modelChart.destroy();
    
    modelChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: [
                    '#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#8b5cf6'
                ],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '75%',
            plugins: {
                legend: { position: 'bottom', labels: { padding: 20, usePointStyle: true } }
            }
        }
    });
}

async function loadModels() {
    const res = await fetch('/v1/admin/models', { headers: authHeaders() });
    if (!res.ok) return;

    const models = await res.json();
    const tbody = document.getElementById('models-tbody');
    tbody.innerHTML = '';

    if (!models.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;opacity:0.5;">No models found</td></tr>';
        return;
    }

    models.forEach(model => {
        const tr = document.createElement('tr');
        const pct = Math.min(100, Math.round((model.utilization || 0) * 100));
        const cap = model.provider_tpm ? `${model.live_tpm || 0} / ${model.provider_tpm}` : `${model.live_tpm || 0}`;
        const status = (model.status || 'active').toUpperCase();
        tr.innerHTML = `
            <td><strong>${model.provider}</strong></td>
            <td><code>${model.model}</code></td>
            <td><code>${model.key_last4 || 'env'}</code></td>
            <td>
                <div class="util-cell">
                    <div class="util-bar"><div class="util-fill ${pct >= 80 ? 'hot' : ''}" style="width:${pct}%"></div></div>
                    <span class="util-meta">${cap} · ${pct}%</span>
                </div>
            </td>
            <td><span class="status-badge ${status === 'ACTIVE' ? 'active' : 'revoked'}">${status}</span></td>
            <td>
                <button class="btn btn-danger btn-icon" onclick="deleteModel('${model.provider}','${model.model}')">Remove</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

window.deleteModel = async function(provider, model) {
    if (!confirm(`Remove ${provider}/${model}?`)) return;
    const res = await fetch(`/v1/admin/models/${encodeURIComponent(provider)}/${encodeURIComponent(model)}`, {
        method: 'DELETE',
        headers: authHeaders(),
    });
    if (res.ok) loadModels();
    else alert('Failed to delete model');
}

function renderSparkline(points) {
    const canvas = document.getElementById('sparklineChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (sparklineChart) sparklineChart.destroy();
    sparklineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: points.map(p => p.label),
            datasets: [{
                label: 'TPM',
                data: points.map(p => p.tokens),
                borderColor: '#14b8a6',
                backgroundColor: 'rgba(20, 184, 166, 0.12)',
                borderWidth: 2,
                tension: 0.35,
                fill: true,
                pointRadius: 0,
                yAxisID: 'y',
            }, {
                label: 'RPM',
                data: points.map(p => p.requests || 0),
                borderColor: '#6366f1',
                borderWidth: 2,
                tension: 0.35,
                fill: false,
                pointRadius: 0,
                yAxisID: 'y1',
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10 } } },
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: 'TPM' } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'RPM' } },
                x: { grid: { display: false } }
            }
        }
    });
}

function modelFormPayload() {
    return {
        provider: document.getElementById('model-provider').value,
        model: document.getElementById('model-id').value,
        api_key: document.getElementById('model-api-key').value,
        base_url: document.getElementById('model-base-url').value,
        provider_rpm: parseInt(document.getElementById('model-rpm').value || '0', 10),
        provider_tpm: parseInt(document.getElementById('model-tpm').value || '0', 10),
        input_per_1m_tokens: parseFloat(document.getElementById('model-in-price').value || '0'),
        output_per_1m_tokens: parseFloat(document.getElementById('model-out-price').value || '0'),
        capabilities: ['chat'],
    };
}

async function testModelConnection() {
    const status = document.getElementById('model-form-status');
    status.textContent = 'Testing…';
    const payload = modelFormPayload();
    const res = await fetch('/v1/admin/models/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ provider: payload.provider, api_key: payload.api_key, base_url: payload.base_url }),
    });
    const data = await res.json();
    status.textContent = data.ok ? 'Connection OK' : (`Failed: ${data.error || 'unhealthy'}`);
}

async function submitAddModel(e) {
    e.preventDefault();
    const status = document.getElementById('model-form-status');
    status.textContent = 'Saving…';
    const res = await fetch('/v1/admin/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(modelFormPayload()),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        status.textContent = err.detail || 'Save failed';
        return;
    }
    document.getElementById('add-model-modal').classList.remove('active');
    document.getElementById('add-model-form').reset();
    status.textContent = '';
    loadModels();
}

async function loadRoutes() {
    const res = await fetch('/v1/admin/routes', { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('routes-editor').value = JSON.stringify(data, null, 2);
}

async function saveRoutes() {
    const status = document.getElementById('routes-status');
    try {
        const payload = JSON.parse(document.getElementById('routes-editor').value);
        const res = await fetch('/v1/admin/routes', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify(payload),
        });
        status.textContent = res.ok ? 'Saved' : 'Save failed';
    } catch (err) {
        status.textContent = `Invalid JSON: ${err.message}`;
    }
}

async function loadHealth() {
    const [liveRes, provRes, alertRes] = await Promise.all([
        fetch('/v1/admin/live', { headers: authHeaders() }),
        fetch('/v1/providers/status', { headers: authHeaders() }),
        fetch('/v1/admin/alerts', { headers: authHeaders() }),
    ]);
    if (liveRes.ok) {
        const live = await liveRes.json();
        document.getElementById('recent-routes').textContent = JSON.stringify(live.recent_routes || [], null, 2);
    }
    if (provRes.ok) {
        const data = await provRes.json();
        const tbody = document.getElementById('health-tbody');
        tbody.innerHTML = '';
        const providers = data.providers || {};
        Object.keys(providers).forEach(name => {
            const p = providers[name];
            const breaker = (p.breaker || {}).state || 'closed';
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${name}</strong></td>
                <td><span class="status-badge ${p.healthy ? 'active' : 'revoked'}">${p.healthy ? 'Healthy' : 'Down'}</span></td>
                <td>${breaker}</td>
            `;
            tbody.appendChild(tr);
        });
    }
    if (alertRes.ok) {
        const alerts = await alertRes.json();
        const list = document.getElementById('webhook-list');
        list.innerHTML = (alerts.webhooks || []).map(u => `<li><code>${u}</code></li>`).join('') || '<li>None</li>';
    }
}

async function addWebhook() {
    const url = document.getElementById('webhook-url').value.trim();
    if (!url) return;
    await fetch('/v1/admin/alerts/webhooks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ url }),
    });
    document.getElementById('webhook-url').value = '';
    loadHealth();
}

function initAnalyticsControls() {
    document.querySelectorAll('#analytics-range .chip').forEach(chip => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('#analytics-range .chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            analyticsRange = chip.getAttribute('data-range');
            loadAnalytics();
            startAnalyticsPolling();
        });
    });
    ['analytics-provider', 'analytics-model', 'analytics-project'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => loadAnalytics());
    });
}

function startAnalyticsPolling() {
    stopAnalyticsPolling();
    if (!['15m', '1h'].includes(analyticsRange)) return;
    analyticsTimer = setInterval(() => loadAnalytics(true), 10000);
}

function stopAnalyticsPolling() {
    if (analyticsTimer) {
        clearInterval(analyticsTimer);
        analyticsTimer = null;
    }
}

function destroyNamedChart(name) {
    if (analyticsCharts[name]) {
        analyticsCharts[name].destroy();
        analyticsCharts[name] = null;
    }
}

function upsertSelect(select, values, current) {
    const keep = current || select.value;
    const existing = new Set(Array.from(select.options).map(o => o.value));
    values.forEach(value => {
        if (!value || existing.has(value)) return;
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = value;
        select.appendChild(opt);
        existing.add(value);
    });
    if (keep) select.value = keep;
}

async function hydrateAnalyticsFilters(data) {
    const providerSel = document.getElementById('analytics-provider');
    const modelSel = document.getElementById('analytics-model');
    const projectSel = document.getElementById('analytics-project');
    const providers = new Set(Object.keys(data.cost_by_provider || {}));
    const models = new Set();
    Object.keys(data.cost_by_model || {}).forEach(label => {
        const parts = label.split('/');
        if (parts.length > 1) providers.add(parts[0]);
        models.add(parts.length > 1 ? parts.slice(1).join('/') : label);
    });
    try {
        const res = await fetch('/v1/admin/models', { headers: authHeaders() });
        if (res.ok) {
            const rows = await res.json();
            rows.forEach(row => {
                if (row.provider) providers.add(row.provider);
                if (row.model) models.add(row.model);
            });
        }
    } catch (e) { /* ignore */ }
    upsertSelect(providerSel, Array.from(providers).sort(), providerSel.value);
    upsertSelect(modelSel, Array.from(models).sort(), modelSel.value);
    upsertSelect(projectSel, Object.keys(data.cost_by_project || {}).sort(), projectSel.value);
}

async function loadAnalytics(silent) {
    const provider = document.getElementById('analytics-provider').value;
    const model = document.getElementById('analytics-model').value;
    const project = document.getElementById('analytics-project').value;
    const params = new URLSearchParams({ range: analyticsRange });
    if (provider) params.set('provider', provider);
    if (model) params.set('model', model);
    if (project) params.set('project', project);
    const res = await fetch(`/v1/admin/analytics?${params}`, { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    if (!silent) await hydrateAnalyticsFilters(data);
    renderAnalytics(data);
}

function chartBase() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, padding: 12 } } },
        scales: {
            y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } },
            x: { grid: { display: false } }
        }
    };
}

function renderNamedChart(name, canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    destroyNamedChart(name);
    analyticsCharts[name] = new Chart(canvas.getContext('2d'), config);
}

function doughnutConfig(labels, values) {
    const safeLabels = labels.length ? labels : ['None'];
    const safeValues = values.length ? values : [1];
    return {
        type: 'doughnut',
        data: {
            labels: safeLabels,
            datasets: [{
                data: safeValues,
                backgroundColor: PALETTE,
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, padding: 12 } } }
        }
    };
}

function stackedAreaDatasets(seriesMap, labels) {
    return Object.keys(seriesMap).map((key, i) => ({
        label: key,
        data: seriesMap[key],
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length] + '99',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 1.5,
    }));
}

function renderAnalytics(data) {
    const totals = data.totals || {};
    document.getElementById('an-requests').textContent = (totals.requests || 0).toLocaleString();
    document.getElementById('an-tokens').textContent = (totals.tokens || 0).toLocaleString();
    document.getElementById('an-cost').textContent = `$${(totals.cost_usd || 0).toFixed(4)}`;
    document.getElementById('an-cache').textContent = `${Math.round((totals.cache_hit_rate || 0) * 100)}%`;
    document.getElementById('an-fallbacks').textContent = (totals.fallbacks || 0).toLocaleString();
    document.getElementById('an-saved').textContent = `$${(totals.cache_cost_avoided_usd || 0).toFixed(4)}`;

    const labels = data.labels || [];
    const throughput = data.throughput || [];
    const tpm = throughput.map(p => p.tpm || 0);
    const rpm = throughput.map(p => p.rpm || 0);
    const cost = throughput.map(p => p.cost || 0);

    renderNamedChart('throughput', 'an-throughput', {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'TPM', data: tpm, borderColor: '#14b8a6', backgroundColor: 'rgba(20,184,166,0.12)', fill: true, tension: 0.35, pointRadius: 0, yAxisID: 'y' },
                { label: 'RPM', data: rpm, borderColor: '#6366f1', fill: false, tension: 0.35, pointRadius: 0, yAxisID: 'y1' }
            ]
        },
        options: {
            ...chartBase(),
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: 'TPM' } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'RPM' } },
                x: { grid: { display: false } }
            }
        }
    });

    const tokenPoints = data.tokens_by_type || [];
    renderNamedChart('tokenTypes', 'an-token-types', {
        type: 'bar',
        data: {
            labels: tokenPoints.map(p => p.label),
            datasets: [
                { label: 'Input', data: tokenPoints.map(p => p.input || 0), backgroundColor: '#6366f1', stack: 't' },
                { label: 'Output', data: tokenPoints.map(p => p.output || 0), backgroundColor: '#ec4899', stack: 't' },
                { label: 'Reasoning', data: tokenPoints.map(p => p.reasoning || 0), backgroundColor: '#f59e0b', stack: 't' },
                { label: 'Cached', data: tokenPoints.map(p => p.cached || 0), backgroundColor: '#14b8a6', stack: 't' }
            ]
        },
        options: { ...chartBase(), scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } } } }
    });

    const byModel = data.tokens_by_model || {};
    renderNamedChart('tokensModel', 'an-tokens-model', {
        type: 'line',
        data: { labels, datasets: stackedAreaDatasets(byModel, labels) },
        options: { ...chartBase(), scales: { x: { grid: { display: false } }, y: { stacked: true, beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } } } }
    });

    renderNamedChart('costTime', 'an-cost-time', {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Requests', data: rpm, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.1)', fill: true, tension: 0.35, pointRadius: 0, yAxisID: 'y' },
                { label: 'Spend', data: cost, borderColor: '#ec4899', fill: false, tension: 0.35, pointRadius: 0, yAxisID: 'y1' }
            ]
        },
        options: {
            ...chartBase(),
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: 'Requests' } },
                y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'USD' } },
                x: { grid: { display: false } }
            }
        }
    });

    const cumulative = data.cumulative_spend || [];
    renderNamedChart('budget', 'an-budget', {
        type: 'line',
        data: {
            labels: cumulative.map(p => p.label),
            datasets: [
                { label: 'Cumulative spend', data: cumulative.map(p => p.spend || 0), borderColor: '#ec4899', fill: true, backgroundColor: 'rgba(236,72,153,0.12)', tension: 0.3, pointRadius: 0 },
                { label: 'Daily budget', data: cumulative.map(p => p.budget || 0), borderColor: '#f59e0b', borderDash: [6, 4], fill: false, pointRadius: 0 },
                { label: 'Remaining', data: cumulative.map(p => p.remaining == null ? null : p.remaining), borderColor: '#14b8a6', fill: false, tension: 0.3, pointRadius: 0 }
            ]
        },
        options: chartBase()
    });

    renderNamedChart('costModel', 'an-cost-model', doughnutConfig(Object.keys(data.cost_by_model || {}), Object.values(data.cost_by_model || {})));
    renderNamedChart('costProvider', 'an-cost-provider', doughnutConfig(Object.keys(data.cost_by_provider || {}), Object.values(data.cost_by_provider || {})));

    const projects = Object.keys(data.cost_by_project || {});
    renderNamedChart('costProject', 'an-cost-project', {
        type: 'bar',
        data: {
            labels: projects,
            datasets: [{ label: 'Cost USD', data: projects.map(k => data.cost_by_project[k]), backgroundColor: '#8b5cf6' }]
        },
        options: { ...chartBase(), indexAxis: 'y' }
    });

    const agents = Object.keys(data.cost_by_agent || {});
    renderNamedChart('costAgent', 'an-cost-agent', {
        type: 'bar',
        data: {
            labels: agents.length ? agents : ['None'],
            datasets: [{ label: 'Cost USD', data: agents.length ? agents.map(k => data.cost_by_agent[k]) : [0], backgroundColor: '#06b6d4' }]
        },
        options: { ...chartBase(), indexAxis: 'y' }
    });

    const reqModels = Object.keys(data.requests_by_model || {});
    renderNamedChart('modelTraffic', 'an-model-traffic', {
        type: 'bar',
        data: {
            labels: reqModels,
            datasets: [{ label: 'Requests', data: reqModels.map(k => data.requests_by_model[k]), backgroundColor: reqModels.map((_, i) => PALETTE[i % PALETTE.length]) }]
        },
        options: { ...chartBase(), indexAxis: 'y' }
    });

    renderNamedChart('routeMix', 'an-route-mix', doughnutConfig(Object.keys(data.route_mix || {}), Object.values(data.route_mix || {})));

    const mixKeys = Object.keys(byModel);
    const totalsPerPoint = labels.map((_, idx) => mixKeys.reduce((sum, key) => sum + ((byModel[key] || [])[idx] || 0), 0));
    const mixDatasets = mixKeys.map((key, i) => ({
        label: key,
        data: (byModel[key] || []).map((val, idx) => totalsPerPoint[idx] ? Math.round((val / totalsPerPoint[idx]) * 1000) / 10 : 0),
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length] + '99',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 1.5,
    }));
    renderNamedChart('trafficMix', 'an-traffic-mix', {
        type: 'line',
        data: { labels, datasets: mixDatasets },
        options: { ...chartBase(), scales: { x: { grid: { display: false } }, y: { stacked: true, min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: '%' } } } }
    });

    const fb = data.fallbacks || [];
    renderNamedChart('fallbacks', 'an-fallbacks-chart', {
        type: 'line',
        data: {
            labels: fb.map(p => p.label),
            datasets: [
                { label: 'Fallbacks', data: fb.map(p => p.fallbacks || 0), borderColor: '#f59e0b', tension: 0.3, pointRadius: 0 },
                { label: '429s', data: fb.map(p => p.rate_limit_hits || 0), borderColor: '#ef4444', tension: 0.3, pointRadius: 0 },
                { label: 'Errors', data: fb.map(p => p.errors || 0), borderColor: '#8b5cf6', tension: 0.3, pointRadius: 0 }
            ]
        },
        options: chartBase()
    });

    const lat = data.latency_over_time || [];
    renderNamedChart('latency', 'an-latency', {
        type: 'line',
        data: {
            labels: lat.map(p => p.label),
            datasets: [
                { label: 'p50', data: lat.map(p => p.p50 || 0), borderColor: '#14b8a6', tension: 0.3, pointRadius: 0 },
                { label: 'p95', data: lat.map(p => p.p95 || 0), borderColor: '#ec4899', tension: 0.3, pointRadius: 0 }
            ]
        },
        options: chartBase()
    });

    const errProv = data.errors_by_provider || {};
    const errLabels = Object.keys(errProv);
    renderNamedChart('errors', 'an-errors', {
        type: 'bar',
        data: {
            labels: errLabels,
            datasets: [
                { label: 'Success', data: errLabels.map(k => (errProv[k] || {}).ok || 0), backgroundColor: '#10b981', stack: 'e' },
                { label: 'Error', data: errLabels.map(k => (errProv[k] || {}).error || 0), backgroundColor: '#ef4444', stack: 'e' }
            ]
        },
        options: { ...chartBase(), indexAxis: 'y', scales: { x: { stacked: true, beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' } }, y: { stacked: true, grid: { display: false } } } }
    });

    const cache = data.cache_hit_rate || [];
    renderNamedChart('cache', 'an-cache-chart', {
        type: 'line',
        data: {
            labels: cache.map(p => p.label),
            datasets: [{ label: 'Hit rate', data: cache.map(p => Math.round((p.rate || 0) * 1000) / 10), borderColor: '#14b8a6', fill: true, backgroundColor: 'rgba(20,184,166,0.12)', tension: 0.3, pointRadius: 0 }]
        },
        options: { ...chartBase(), scales: { y: { beginAtZero: true, max: 100, grid: { color: 'rgba(255,255,255,0.05)' }, title: { display: true, text: '%' } }, x: { grid: { display: false } } } }
    });

    const quota = data.quota_by_model || [];
    renderNamedChart('quota', 'an-quota', {
        type: 'bar',
        data: {
            labels: quota.map(q => q.label),
            datasets: [
                { label: 'Live TPM', data: quota.map(q => q.tpm || 0), backgroundColor: '#14b8a6' },
                { label: 'Cap', data: quota.map(q => q.tpm_limit || 0), backgroundColor: 'rgba(99,102,241,0.35)' }
            ]
        },
        options: { ...chartBase(), indexAxis: 'y' }
    });
}
