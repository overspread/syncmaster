// SyncMaster App - client-side JS
function runSync(direction) {
    const target = 'OCI 187';
    fetch('/sync?target=' + encodeURIComponent(target) + '&dir=' + direction, { method: 'POST' });
}

function cancelSync() {
    fetch('/cancel', { method: 'POST' });
}

// 定时刷新统计
setInterval(() => {
    fetch('/api/stats').then(r => r.json()).then(data => {
        if (data.pending !== undefined) {
            document.getElementById('stat-pending') && (document.getElementById('stat-pending').textContent = data.pending);
        }
    }).catch(() => {});
}, 5000);