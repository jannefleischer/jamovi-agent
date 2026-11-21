/*
 * Script for the jamovi main page (index.html gets
 * <script src="../agent/static/page.js"> at image build). Two jobs:
 *
 * 1. Module variant: jamovi puts a transparent cover over every result that
 *    is not the selected analysis; a click on it only selects the analysis.
 *    When the assistant runs an analysis, the client selects the new one, so
 *    the chat would no longer take clicks. The covers of assistant results
 *    are switched off, so the chat is always usable.
 * 2. Sidebar variant: only if agent/health reports ui "sidebar" or "both"
 *    (JAMOVI_AGENT_UI).
 *
 * The main page is served at <root>/<instance>/, so the agent API is at
 * ../agent/ and the instance id is the last path segment.
 */
(function () {
    'use strict';
    const style = document.createElement('style');
    style.textContent =
        ".jmv-results-container[data-analysis-name='assistant'] .jmv-results-cover { display: none; }";
    document.head.appendChild(style);

    const base = new URL('../agent/', window.location.href).href;
    const match = window.location.pathname.match(/\/([0-9a-f-]{36})\/?$/);
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

    async function start() {
        let health;
        try {
            const r = await fetch(base + 'health', { credentials: 'same-origin' });
            health = await r.json();
        }
        catch (e) {
            return;  // agent not available
        }
        if (!health.enabled || !['sidebar', 'both'].includes(health.ui))
            return;

        await Promise.all([
            load('link', { rel: 'stylesheet', href: base + 'static/chat.css' }),
            load('script', { src: base + 'static/chat.js' }),
        ]);

        const panel = document.createElement('div');
        panel.id = 'jamovi-assistant-sidebar';
        const toggle = document.createElement('button');
        toggle.id = 'jamovi-assistant-toggle';
        toggle.textContent = 'Assistant';
        document.body.append(panel, toggle);

        let mounted = false;
        toggle.addEventListener('click', () => {
            panel.classList.toggle('open');
            toggle.textContent = panel.classList.contains('open') ? 'Close' : 'Assistant';
            if (!mounted) {
                window.JamoviAssistant.mount(panel, { base, instanceId, title: 'jamovi-assistant' });
                mounted = true;
            }
        });
    }

    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', start);
    else
        start();
})();
