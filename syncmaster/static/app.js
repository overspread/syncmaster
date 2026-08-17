// SyncMaster App - client-side JS

// ── 同步操作 ──
function runSync(direction) {
    const target = 'OCI 187';
    fetch('/sync?target=' + encodeURIComponent(target) + '&dir=' + direction, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (!d.ok && d.message) {
                console.log(d.message);
            }
        })
        .catch(() => {});
}

function cancelSync() {
    fetch('/cancel', { method: 'POST' }).catch(() => {});
}

// ── 定时刷新统计 ──
setInterval(() => {
    fetch('/api/stats').then(r => r.json()).then(data => {
        if (data.pending !== undefined) {
            const el = document.getElementById('stat-pending');
            if (el) el.textContent = data.pending;
        }
    }).catch(() => {});
}, 5000);

// ── SSE 事件已在 base.html 中处理，这里补充监控面板的 SSE 响应 ──
// base.html 的 SSE onmessage 已处理 log / done / started 事件
// 如果当前页是监控面板，也更新进度
if (typeof EventSource !== 'undefined') {
    // 补充：done 事件时刷新监控数据
    const origOnMessage = window.__smSseHandler;
}
