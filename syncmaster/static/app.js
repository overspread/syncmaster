// SyncMaster App - client-side JS

function _fetch(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers["Authorization"] = "Bearer " + (window.SM_TOKEN || "");
    return fetch(url, opts);
}

// ── 同步操作 ──
function runSync(direction, trigger) {
    const btn = trigger || null;
    if (btn && btn.disabled) return;
    const origText = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> 同步中...'; }
    _fetch('/sync?dir=' + direction, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.ok) {
                if (btn) { btn.disabled = false; btn.innerHTML = origText; }
                alert(d.message || '同步启动失败');
                return;
            }
            // 同步已启动：显示状态提示（后续由 SSE started/done 事件接管）
            const el = document.getElementById('last-sync-time');
            if (el) el.textContent = '同步中...';
            if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> 同步中...'; }
            if (window.SM_TOKEN) {
                const es = new EventSource('/events?token=' + window.SM_TOKEN);
                es.onmessage = e => {
                    try {
                        const ev = JSON.parse(e.data);
                        if (ev.type === 'done') {
                            es.close();
                            if (btn) { btn.disabled = false; btn.innerHTML = origText; }
                            if (ev.success) {
                                const t = document.getElementById('last-sync-time');
                                if (t) t.textContent = '完成 ' + new Date().toLocaleTimeString();
                                setTimeout(() => location.reload(), 1500);
                            } else {
                                alert('同步失败：' + (ev.message || '请查看监控面板日志'));
                            }
                        }
                    } catch (err) { /* ignore malformed events */ }
                };
                es.onerror = () => { es.close(); if (btn) { btn.disabled = false; btn.innerHTML = origText; } };
            }
        })
        .catch(() => {
            if (btn) { btn.disabled = false; btn.innerHTML = origText; }
            alert('请求失败，请检查服务是否运行');
        });
}

function cancelSync() {
    _fetch('/cancel', { method: 'POST' }).catch(() => {});
}

// ── 设置页：加载已保存配置 ──
function loadConfig() {
    _fetch('/api/config').then(r => r.json()).then(cfg => {
        const set = (id, val) => { const el = document.getElementById(id); if (el && val) el.value = val; };
        set('jumpHost', cfg.jumpHost);
        set('jumpKey', cfg.jumpKey);
        set('remoteUser', cfg.remoteUser);
        set('remoteHost', cfg.remoteHost);
        set('remoteKey', cfg.remoteKey);
        set('remoteDir', cfg.remoteDir);
        set('localDir', cfg.localDir);
    }).catch(() => {});
}

// ── 设置页：保存配置 ──
function saveConfig() {
    const val = id => (document.getElementById(id) || {}).value || '';
    const payload = {
        jumpHost: val('jumpHost'),
        jumpKey: val('jumpKey'),
        remoteUser: val('remoteUser'),
        remoteHost: val('remoteHost'),
        remoteKey: val('remoteKey'),
        remoteDir: val('remoteDir'),
        localDir: val('localDir'),
    };
    const btn = document.getElementById('btnSaveCfg');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> 保存中...'; }
    _fetch('/api/config/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            if (btn) btn.innerHTML = '<i class="fa fa-check"></i> 已保存';
            setTimeout(() => { if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存配置'; }, 2000);
        } else {
            alert(d.message || '保存失败');
            if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存配置';
        }
    })
    .catch(() => {
        alert('请求失败，服务未响应');
        if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存配置';
    })
    .finally(() => { if (btn) btn.disabled = false; });
}

// ── 测试连接服务器 ──
function testConnection() {
    const status = document.getElementById('connStatus');
    const btn = document.getElementById('btnTestConn');
    const val = id => (document.getElementById(id) || {}).value || '';
    const payload = {
        jumpHost: val('jumpHost'),
        jumpKey: val('jumpKey'),
        remoteUser: val('remoteUser'),
        remoteHost: val('remoteHost'),
        remoteKey: val('remoteKey'),
    };
    if (!payload.jumpHost || !payload.remoteHost) {
        status.className = 'conn-status err';
        status.textContent = '请先填跳板机与服务器地址';
        return;
    }
    status.className = 'conn-status testing';
    status.textContent = '连接中…';
    if (btn) btn.disabled = true;
    _fetch('/api/test_connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(d => {
        if (d.jumpOk && d.remoteOk) {
            status.className = 'conn-status ok';
            status.textContent = '✓ 跳板机 & 服务器 均连通';
        } else if (d.jumpOk && !d.remoteOk) {
            status.className = 'conn-status err';
            status.textContent = '✓ 跳板机通 / ✗ 服务器:' + (d.remoteMsg || '失败');
        } else {
            status.className = 'conn-status err';
            status.textContent = '✗ 跳板机:' + (d.jumpMsg || '失败');
        }
    })
    .catch(() => {
        status.className = 'conn-status err';
        status.textContent = '请求失败，服务未响应';
    })
    .finally(() => { if (btn) btn.disabled = false; });
}

// ── syncignore 规则：加载与保存 ──
function loadIgnoreRules() {
    _fetch('/api/syncignore').then(r => r.json()).then(d => {
        if (d.ok && d.rules) {
            const el = document.getElementById('syncignoreEditor');
            if (el) el.value = d.rules.join('\n');
        }
    }).catch(() => {});
}

function saveIgnoreRules() {
    const el = document.getElementById('syncignoreEditor');
    if (!el) return;
    const rules = el.value.split('\n').map(s => s.trim()).filter(s => s);
    const btn = document.getElementById('btnSaveIgnore');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> 保存中...'; }
    _fetch('/api/syncignore/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rules }),
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            if (btn) btn.innerHTML = '<i class="fa fa-check"></i> 已保存';
            setTimeout(() => { if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存规则'; }, 2000);
        } else {
            alert(d.message || '保存失败');
            if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存规则';
        }
    })
    .catch(() => {
        alert('请求失败');
        if (btn) btn.innerHTML = '<i class="fa fa-save"></i> 保存规则';
    })
    .finally(() => { if (btn) btn.disabled = false; });
}

// ── 路径输入框：请求后端打开 macOS 原生 Finder ──
function pickPath(inputId, kind) {
    const el = document.getElementById(inputId);
    if (!el) return;
    _fetch('/api/pick_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: kind === 'dir' ? 'directory' : 'file' })
    })
    .then(function (r) {
        if (!r.ok) throw new Error('请求失败（HTTP ' + r.status + '）');
        return r.json();
    })
    .then(function (data) {
        if (!data.ok) throw new Error(data.message || '打开 Finder 失败');
        if (data.path) el.value = data.path;
    })
    .catch(function (e) {
        console.error('[SyncMaster] 文件选择失败', e);
    });
}

// ── 自动持续同步：加载状态与切换 ──
function loadAutoSync() {
    const toggle = document.getElementById('autoSyncToggle');
    if (!toggle) return;
    _fetch('/api/autosync').then(r => r.json()).then(d => {
        toggle.checked = !!d.enabled;
        renderAutoSyncStatus(d);
        updateAutoSyncDir(d.direction || 'bidirectional');
    }).catch(() => {});
}

function renderAutoSyncStatus(d) {
    const el = document.getElementById('autoSyncStatus');
    if (!el) return;
    const parts = [];
    const dirLabel = d.direction === 'push' ? '本地 → 远程'
        : d.direction === 'pull' ? '远程 → 本地' : '双向同步';
    if (d.enabled) {
        parts.push('<span style="color:#1ca672;">● 已开启</span>');
        parts.push('方向：' + dirLabel);
        parts.push('模式：' + (d.mode === 'poll' ? '实时轮询监听' : d.mode));
        if (d.lastSyncAt > 0) {
            parts.push('上次自动同步：' + new Date(d.lastSyncAt * 1000).toLocaleTimeString());
        }
        if (d.reason) {
            parts.push('<span style="color:#e5484d;">最近失败：' + d.reason + '（将自动重试）</span>');
        }
        el.innerHTML = parts.join(' · ');
    } else {
        el.innerHTML = '开启后，本地文件变化将按所选方向自动同步到服务器';
    }
}

function updateAutoSyncDir(direction) {
    const sel = document.getElementById('autoSyncDir');
    if (sel) sel.value = direction;
}

function setAutoSyncDirection(direction) {
    const sel = document.getElementById('autoSyncDir');
    if (sel) sel.disabled = true;
    _fetch('/api/autosync/direction?direction=' + direction, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                updateAutoSyncDir(d.direction || direction);
                _fetch('/api/autosync').then(r => r.json()).then(s => renderAutoSyncStatus(s)).catch(() => {});
            } else {
                updateAutoSyncDir(sel && sel.dataset.prev || direction);
                alert(d.message || '切换方向失败');
            }
        })
        .catch(() => alert('请求失败，服务未响应'))
        .finally(() => { if (sel) sel.disabled = false; });
}

function setAutoSync(enabled) {
    const toggle = document.getElementById('autoSyncToggle');
    if (toggle) toggle.disabled = true;
    const sel = document.getElementById('autoSyncDir');
    const direction = sel ? sel.value : 'bidirectional';
    _fetch('/api/autosync/toggle?enabled=' + (enabled ? 1 : 0) + '&direction=' + direction, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                if (toggle) toggle.checked = enabled;
                renderAutoSyncStatus({ enabled: enabled, mode: d.mode || '', reason: d.reason || '', direction: d.direction || direction });
                updateAutoSyncDir(d.direction || direction);
            } else {
                if (toggle) toggle.checked = !enabled;
                alert(d.message || '切换失败');
            }
        })
        .catch(() => {
            if (toggle) toggle.checked = !enabled;
            alert('请求失败，服务未响应');
        })
        .finally(() => { if (toggle) toggle.disabled = false; });
}

// ── 页面初始化 ──
document.addEventListener('DOMContentLoaded', function() {
    if (document.getElementById('jumpHost')) {
        loadConfig();
        loadIgnoreRules();
        loadAutoSync();
    }
});

// ── 定时刷新统计 ──
setInterval(() => {
    _fetch('/api/stats').then(r => r.json()).then(data => {
        if (data.pending !== undefined) {
            const el = document.getElementById('stat-pending');
            if (el) el.textContent = data.pending;
        }
    }).catch(() => {});
}, 5000);