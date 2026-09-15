// SyncMaster App - 公共工具函数（页面交互由 Alpine.js 驱动）

function _fetch(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers["Authorization"] = "Bearer " + (window.SM_TOKEN || "");
    return fetch(url, opts);
}
