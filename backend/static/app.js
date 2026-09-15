// SyncMaster App - 公共工具函数（页面交互由 Alpine.js 驱动）

function _fetch(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers["Authorization"] = "Bearer " + (window.SM_TOKEN || "");
    return fetch(url, opts);
}

// 非阻塞 toast 通知。type: 'error' | 'success' | 'info'，默认 info。
// ms: 显示时长（默认 3200ms）。替代阻塞式 alert()，符合项目"禁止阻塞弹窗"约定。
function notify(msg, type, ms) {
    type = type || 'info';
    let wrap = document.querySelector('.toast-container');
    if (!wrap) {
        wrap = document.createElement('div');
        wrap.className = 'toast-container';
        document.body.appendChild(wrap);
    }
    const t = document.createElement('div');
    t.className = 'toast toast-' + type;
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(() => {
        t.classList.add('toast-out');
        setTimeout(() => t.remove(), 260);
    }, ms || 3200);
}
