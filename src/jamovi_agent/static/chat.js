/*
 * Chat widget of the jamovi agent, shared by both UIs:
 *  - module variant: mounted inside the results of the jamoviassistant analysis (module jamovi-assistant)
 *  - sidebar variant: mounted in a panel next to the jamovi main window
 *
 * JamoviAssistant.mount(element, { base, instanceId, title })
 *   base        URL of the agent API ending in "agent/" (relative is fine)
 *   instanceId  jamovi instance (window) id
 *
 * Talks to: GET history, POST chat (Server-Sent Events), POST reset.
 * No dependencies; works under jamovi's CSP (same origin only).
 */
(function () {
    'use strict';
    if (window.JamoviAssistant)
        return;

    const TEXT = {
        placeholder: 'Frage zu deinen Daten oder zur Statistik …',
        send: 'Senden',
        reset: 'Neu',
        resetTitle: 'Gespräch löschen',
        thinking: 'Denkt nach …',
        empty: 'Frag zum Beispiel: „Unterscheidet sich X zwischen den Gruppen von Y?“ '
             + 'Analysen, die der Assistent rechnet, erscheinen im Ergebnisbereich.',
        error: 'Fehler: ',
        tools: {
            get_dataset_schema: 'sieht sich den Datensatz an',
            describe_variables: 'berechnet Kennwerte',
            get_data: 'liest Daten',
            search_analyses: 'sucht eine passende Analyse',
            get_analysis_schema: 'prüft die Optionen',
            list_session_analyses: 'sieht sich die vorhandenen Analysen an',
            create_analysis: 'rechnet eine Analyse',
            update_analysis: 'ändert eine Analyse',
            get_results: 'liest Ergebnisse',
            search_docs: 'schlägt in der Dokumentation nach',
            read_doc: 'liest Dokumentation',
            explain_analysis: 'liest die Beschreibung der Analyse',
            list_instances: 'sucht das jamovi-Fenster',
        },
    };

    // ----- minimal Markdown (escaped first, so no HTML from the LLM gets through) -----

    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function inline(s) {
        return s
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
    }

    function markdown(src) {
        const lines = esc(src || '').split('\n');
        const out = [];
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
                const row = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
                const head = row(line);
                i += 2;
                const body = [];
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]))
                    body.push(row(lines[i++]));
                out.push('<table><thead><tr>' + head.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>'
                    + body.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('')
                    + '</tbody></table>');
                continue;
            }
            if (/^```/.test(line)) {
                const code = [];
                i++;
                while (i < lines.length && !/^```/.test(lines[i]))
                    code.push(lines[i++]);
                i++;
                out.push('<pre>' + code.join('\n') + '</pre>');
                continue;
            }
            const h = line.match(/^(#{1,4})\s+(.*)$/);
            if (h) {
                out.push(`<h${Math.min(h[1].length + 2, 6)}>${inline(h[2])}</h${Math.min(h[1].length + 2, 6)}>`);
                i++;
                continue;
            }
            if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
                const ordered = /^\s*\d+\./.test(line);
                const items = [];
                while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i]))
                    items.push(inline(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, '')));
                const tag = ordered ? 'ol' : 'ul';
                out.push(`<${tag}>` + items.map(t => `<li>${t}</li>`).join('') + `</${tag}>`);
                continue;
            }
            if (line.trim() === '') {
                i++;
                continue;
            }
            const para = [];
            while (i < lines.length && lines[i].trim() !== '' && !/^(#{1,4}\s|```|\s*([-*]|\d+\.)\s|\s*\|)/.test(lines[i]))
                para.push(inline(lines[i++]));
            out.push('<p>' + para.join('<br>') + '</p>');
        }
        return out.join('');
    }

    // ----- widget --------------------------------------------------------------------

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    async function* sse(response) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
                const chunk = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                const data = chunk.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5)).join('\n');
                if (data) {
                    try { yield JSON.parse(data); } catch (e) { /* ignore broken event */ }
                }
            }
        }
    }

    function mount(root, options) {
        const base = options.base;
        const instanceId = options.instanceId;
        root.classList.add('jamovi-assistant');
        root.innerHTML = '';

        const log = el('div', 'jamovi-assistant-log');
        // no <form>: jamovi's results frame is sandboxed without allow-forms,
        // which blocks form submission (the submit event never fires)
        const form = el('div', 'jamovi-assistant-form');
        const input = el('textarea', 'jamovi-assistant-input');
        input.rows = 2;
        input.placeholder = TEXT.placeholder;
        const send = el('button', 'jamovi-assistant-send', TEXT.send);
        send.type = 'button';
        const reset = el('button', 'jamovi-assistant-reset', TEXT.reset);
        reset.type = 'button';
        reset.title = TEXT.resetTitle;
        form.append(input, send, reset);
        if (options.title) root.append(el('div', 'jamovi-assistant-title', options.title));
        root.append(log, form);

        const q = instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : '';

        function add(role, html, text) {
            const m = el('div', 'jamovi-assistant-msg ' + (role === 'assistant' ? 'jamovi-bot' : 'jamovi-assistant-' + role));
            if (html !== undefined) m.innerHTML = html;
            else m.textContent = text;
            log.append(m);
            log.scrollTop = log.scrollHeight;
            return m;
        }

        function showEmpty() {
            if (!log.children.length)
                add('hint', undefined, TEXT.empty);
        }

        async function loadHistory() {
            try {
                const r = await fetch(base + 'history' + q, { credentials: 'same-origin' });
                if (!r.ok) throw new Error(r.status);
                const msgs = await r.json();
                log.innerHTML = '';
                for (const m of msgs)
                    add(m.role, m.role === 'assistant' ? markdown(m.content) : undefined, m.content);
            }
            catch (e) { /* no history yet */ }
            showEmpty();
        }

        async function ask(text) {
            log.querySelectorAll('.jamovi-assistant-hint').forEach(n => n.remove());
            add('user', undefined, text);
            const status = add('status', undefined, TEXT.thinking);
            input.disabled = send.disabled = reset.disabled = true;
            try {
                const r = await fetch(base + 'chat', {
                    method: 'POST', credentials: 'same-origin',
                    // exactly this Accept value makes jupyter-server-proxy stream the
                    // response instead of buffering it until the turn is finished
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                    body: JSON.stringify({ message: text, instance_id: instanceId || undefined }),
                });
                if (!r.ok) {
                    let msg = r.status + '';
                    try { msg = (await r.json()).error || msg; } catch (e) { /* not JSON */ }
                    throw new Error(msg);
                }
                for await (const ev of sse(r)) {
                    if (ev.type === 'tool_call')
                        status.textContent = '… ' + (TEXT.tools[ev.name] || ev.name);
                    else if (ev.type === 'message')
                        add('assistant', markdown(ev.content));
                    else if (ev.type === 'error')
                        add('error', undefined, TEXT.error + ev.message);
                }
            }
            catch (e) {
                add('error', undefined, TEXT.error + e.message);
            }
            finally {
                status.remove();
                input.disabled = send.disabled = reset.disabled = false;
                input.focus();
            }
        }

        function submit() {
            const text = input.value.trim();
            if (!text || send.disabled) return;
            input.value = '';
            ask(text);
        }
        send.addEventListener('click', submit);
        input.addEventListener('keydown', ev => {
            if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault();
                submit();
            }
            ev.stopPropagation();  // keep jamovi shortcuts out of the text field
        });
        reset.addEventListener('click', async () => {
            await fetch(base + 'reset', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ instance_id: instanceId || undefined }),
            });
            log.innerHTML = '';
            showEmpty();
        });

        loadHistory();
        return { reload: loadHistory };
    }

    window.JamoviAssistant = { mount, markdown };
})();
