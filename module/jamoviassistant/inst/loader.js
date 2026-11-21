/*
 * Loader for the jamovi-assistant module: the results frame is served at
 * <root>/r/<instance>/<analysis>/, the agent at <root>/agent/. Loads the
 * shared widget from the agent and mounts it into every placeholder.
 */
(function () {
    'use strict';
    if (window.__jamoviAssistantLoader)
        return;
    window.__jamoviAssistantLoader = true;
    const base = new URL('../../../agent/', window.location.href).href;
    const match = window.location.pathname.match(/\/r\/([0-9a-f-]{36})\//);
    const instanceId = match ? match[1] : '';

    function load(tag, attrs) {
        return new Promise((resolve, reject) => {
            const e = document.createElement(tag);
            Object.assign(e, attrs);
            e.onload = resolve;
            e.onerror = reject;
            document.head.appendChild(e);
        });
    }

    let ready = null;
    function widget() {
        if (!ready)
            ready = fetch(base + 'health', { credentials: 'same-origin' })
                .then(r => r.json())
                .then(h => {
                    if (!h.enabled || !['module', 'both'].includes(h.ui))
                        throw new Error('The chat in the results is switched off (JAMOVI_AGENT_UI=' + h.ui + ').');
                    if (window.JamoviAssistant) return;
                    return Promise.all([
                        load('link', { rel: 'stylesheet', href: base + 'static/chat.css' }),
                        load('script', { src: base + 'static/chat.js' }),
                    ]);
                });
        return ready;
    }

    function mountAll() {
        document.querySelectorAll('.jamovi-assistant-module:not([data-mounted])').forEach(el => {
            el.setAttribute('data-mounted', '1');
            const host = document.createElement('div');
            el.appendChild(host);
            widget().then(() => window.JamoviAssistant.mount(host, { base, instanceId }))
                .catch(e => { host.textContent = e.message || 'jamovi-assistant is not available.'; });
        });
    }

    // the content is inserted after the script has loaded, and again on every re-run
    new MutationObserver(mountAll).observe(document.body, { childList: true, subtree: true });
    mountAll();
})();
