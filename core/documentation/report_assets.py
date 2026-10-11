"""
Report Assets Manager
Provides minified CSS and JS for HTML reports via base64 data URIs.
This replaces inline styles and scripts to dramatically reduce file sizes.
"""

import re


def minify_css(css: str) -> str:
    """Basic CSS minification."""
    # Remove comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Remove newlines and extra spaces
    css = re.sub(r"\s+", " ", css)
    # Remove spaces around special characters
    css = re.sub(r"\s*([{}:;,>+~])\s*", r"\1", css)
    return css.strip()


def minify_js(js: str) -> str:
    """Basic JS minification."""
    # Remove single-line comments (but preserve URLs)
    js = re.sub(r"(?<!:)//[^\n]*", "", js)
    # Remove multi-line comments
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    # Remove excess whitespace
    js = re.sub(r"\s+", " ", js)
    # Remove spaces around operators and punctuation
    js = re.sub(r"\s*([{}();,=<>!+\-*/&|])\s*", r"\1", js)
    return js.strip()


def get_css_styles() -> str:
    """Get minified CSS as data URI."""
    css = """
* {margin:0;padding:0;box-sizing:border-box;}
body {font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:#f8f9fa;color:#212529;line-height:1.6;padding:20px;}
.container {max-width:1400px;margin:0 auto;}
.table-badge,.folder-badge {font-size:0.8rem;padding:3px 8px;background:#e9ecef;color:#495057;border-radius:4px;margin-left:8px;}
.dax-box {margin-top:10px;padding:15px;background:#f8f9fa;border-radius:6px;border-left:4px solid #dee2e6;}
.dax-box.added {border-left-color:#28a745;background:#f0f9f4;}
.dax-box.removed {border-left-color:#dc3545;background:#fff5f5;}
.dax-box pre {margin:0;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;white-space:pre-wrap;word-wrap:break-word;}
.dax-comparison {display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:10px;}
.dax-side {border:1px solid #dee2e6;border-radius:6px;overflow:hidden;}
.dax-side.before {border-left:4px solid #dc3545;}
.dax-side.after {border-left:4px solid #28a745;}
.dax-label {padding:8px 12px;font-weight:600;font-size:0.85rem;text-transform:uppercase;letter-spacing:0.5px;background:#e9ecef;color:#495057;}
.dax-side.before .dax-label {background:#f8d7da;color:#721c24;}
.dax-side.after .dax-label {background:#d4edda;color:#155724;}
.dax-side pre {margin:0;padding:12px;background:#f8f9fa;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;white-space:pre-wrap;word-wrap:break-word;}
.metadata-changes {margin-top:10px;padding:10px;background:#f8f9fa;border-radius:6px;}
.metadata-row {padding:6px;display:flex;gap:10px;align-items:center;font-size:0.9rem;}
.old {color:#dc3545;text-decoration:line-through;}
.new {color:#28a745;font-weight:600;}
.section.collapsible-section .section-body {display:none;}
.section.collapsible-section.expanded .section-body {display:block;}
.section-header.clickable {cursor:pointer;user-select:none;display:flex;align-items:center;gap:10px;}
.section-header.clickable:hover {background:#f8f9fa;}
.header {background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:40px;border-radius:12px;margin-bottom:30px;box-shadow:0 4px 6px rgba(0,0,0,0.1);}
.header h1 {font-size:2.5rem;margin-bottom:20px;}
.models {display:flex;align-items:center;gap:15px;margin:20px 0;}
.model-badge {padding:8px 16px;border-radius:6px;font-weight:600;}
.model-badge.old {background:rgba(255,255,255,0.2);}
.model-badge.new {background:rgba(255,255,255,0.3);}
.vs {font-size:1.2rem;opacity:0.8;}
.timestamp {opacity:0.9;margin-top:10px;}
.summary-card {background:white;padding:30px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;}
.summary-card h2 {margin-bottom:20px;color:#495057;}
.stat-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px;}
.stat-item {text-align:center;padding:15px;background:#f8f9fa;border-radius:8px;}
.stat-value {font-size:2rem;font-weight:700;margin-bottom:5px;}
.stat-value.added {color:#28a745;}
.stat-value.removed {color:#dc3545;}
.stat-value.modified {color:#ffc107;}
.stat-label {font-size:0.85rem;color:#6c757d;text-transform:uppercase;letter-spacing:0.5px;}
.tabs {display:flex;gap:10px;margin-bottom:20px;border-bottom:2px solid #dee2e6;}
.tab-button {background:none;border:none;padding:12px 24px;font-size:1rem;cursor:pointer;color:#6c757d;border-bottom:3px solid transparent;transition:all 0.3s;}
.tab-button:hover {color:#495057;background:#f8f9fa;}
.tab-button.active {color:#667eea;border-bottom-color:#667eea;font-weight:600;}
.tab-pane {display:none;}
.tab-pane.active {display:block;}
.section {background:white;padding:30px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;}
.section h2 {margin-bottom:20px;color:#495057;padding-bottom:10px;border-bottom:2px solid #dee2e6;}
.changes-list {display:flex;flex-direction:column;gap:15px;}
.change-card {background:#f8f9fa;border-radius:8px;overflow:hidden;border-left:4px solid #dee2e6;transition:all 0.3s;}
.change-card.added {border-left-color:#28a745;background:#f0f9f4;}
.change-card.removed {border-left-color:#dc3545;background:#fff5f5;}
.change-card.modified {border-left-color:#ffc107;background:#fffbf0;}
.change-card.expanded .change-body {display:block;}
.change-card.expanded .expand-icon {transform:rotate(180deg);}
.change-header {padding:15px 20px;display:flex;align-items:center;gap:12px;}
.change-header.clickable {cursor:pointer;user-select:none;}
.change-header.clickable:hover {background:rgba(0,0,0,0.02);}
.badge {padding:4px 10px;border-radius:4px;font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;}
.badge.added {background:#28a745;color:white;}
.badge.removed {background:#dc3545;color:white;}
.badge.modified {background:#ffc107;color:#000;}
.badge.mini {padding:2px 6px;font-size:0.7rem;}
.item-name {font-size:1.05rem;flex:1;}
.meta {color:#6c757d;font-size:0.9rem;}
.expand-icon {color:#6c757d;transition:transform 0.3s;}
.change-body {display:none;padding:0 20px 20px 20px;}
.sub-section {margin-top:15px;}
.sub-section-title {font-weight:600;margin-bottom:10px;color:#495057;font-size:0.95rem;}
.sub-items {display:flex;flex-direction:column;gap:8px;}
.sub-item {padding:10px 15px;background:white;border-radius:6px;display:flex;align-items:center;gap:10px;border-left:3px solid transparent;}
.sub-item.added {border-left-color:#28a745;background:#f0f9f4;}
.sub-item.removed {border-left-color:#dc3545;background:#fff5f5;}
.sub-item.modified {border-left-color:#ffc107;background:#fffbf0;}
.type {color:#6c757d;font-size:0.85rem;padding:2px 8px;background:rgba(0,0,0,0.05);border-radius:4px;}
.diff-badge {padding:2px 6px;font-size:0.7rem;background:#e9ecef;color:#495057;border-radius:3px;font-weight:600;}
.dax-expression {margin-top:10px;padding:12px;border-radius:6px;border-left:4px solid #dee2e6;background:#f8f9fa;}
.dax-expression.added {border-left-color:#28a745;background:#f0f9f4;}
.dax-expression.removed {border-left-color:#dc3545;background:#fff5f5;}
.dax-expression pre {margin:0;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;white-space:pre-wrap;word-wrap:break-word;}
.dax-mini-diff {display:grid;grid-template-columns:1fr 1fr;gap:15px;margin-top:10px;}
.dax-before,.dax-after {border-radius:6px;overflow:hidden;border:1px solid #dee2e6;}
.dax-before {border-left:4px solid #dc3545;}
.dax-after {border-left:4px solid #28a745;}
.dax-before .label,.dax-after .label {padding:6px 12px;font-weight:600;font-size:0.85rem;text-transform:uppercase;letter-spacing:0.5px;}
.dax-before .label {background:#f8d7da;color:#721c24;}
.dax-after .label {background:#d4edda;color:#155724;}
.dax-before pre,.dax-after pre {margin:0;padding:12px;background:#f8f9fa;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;white-space:pre-wrap;word-wrap:break-word;}
.tmdl-section {background:white;padding:30px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);}
.tmdl-controls {margin-bottom:15px;display:flex;gap:15px;align-items:center;}
.tmdl-controls label {display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none;}
.tmdl-split-view {display:grid;grid-template-columns:1fr 1fr;gap:20px;height:70vh;}
.tmdl-pane {border:1px solid #dee2e6;border-radius:8px;overflow:hidden;display:flex;flex-direction:column;}
.tmdl-pane-header {background:#495057;color:white;padding:12px 20px;font-weight:600;font-size:0.95rem;}
.tmdl-code-container {flex:1;overflow:auto;background:#f8f9fa;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;}
.tmdl-code {padding:10px 0;}
.tmdl-line {display:flex;padding:2px 10px;cursor:pointer;transition:background 0.2s;}
.tmdl-line:hover {background:#e9ecef;}
.tmdl-line.highlight {background:#fff3cd !important;}
.line-number {display:inline-block;width:60px;color:#6c757d;text-align:right;padding-right:15px;user-select:none;border-right:1px solid #dee2e6;margin-right:15px;}
.line-content {flex:1;white-space:pre;word-wrap:break-word;}
.diff-view-toggle {display:flex;gap:10px;margin-bottom:20px;}
.toggle-btn {flex:1;padding:12px 20px;border:2px solid #dee2e6;background:white;border-radius:8px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;font-size:1rem;font-weight:600;transition:all 0.3s;}
.toggle-btn:hover {border-color:#667eea;color:#667eea;}
.toggle-btn.active {background:#667eea;color:white;border-color:#667eea;}
.toggle-icon {font-size:1.2rem;}
.diff-view {display:none;}
.diff-view.active {display:block;}
.semantic-diff-container {display:flex;flex-direction:column;gap:20px;}
.change-group {background:#f8f9fa;padding:20px;border-radius:8px;border-left:4px solid #dee2e6;}
.change-group.added {border-left-color:#28a745;}
.change-group.removed {border-left-color:#dc3545;}
.change-group.modified {border-left-color:#ffc107;}
.change-group-title {font-weight:700;font-size:1.1rem;margin-bottom:15px;display:flex;align-items:center;gap:10px;}
.change-items {display:flex;flex-direction:column;gap:12px;}
.change-item {background:white;padding:15px;border-radius:6px;border-left:3px solid transparent;}
.change-item.added {border-left-color:#28a745;}
.change-item.removed {border-left-color:#dc3545;}
.change-item.modified {border-left-color:#ffc107;}
.change-item.expandable .change-item-body {display:none;}
.change-item.expandable.expanded .change-item-body {display:block;margin-top:10px;}
.change-item.expandable.expanded .expand-icon {transform:rotate(180deg);}
.change-item-name {font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:8px;}
.change-item-name.clickable {cursor:pointer;user-select:none;}
.change-item-name.clickable:hover {background:rgba(0,0,0,0.02);margin:-5px;padding:5px;border-radius:4px;}
.change-item-body {padding-top:10px;}
.change-detail {font-size:0.9rem;color:#6c757d;margin-top:5px;}
.tmdl-diff-container {background:#f8f9fa;padding:20px;border-radius:8px;font-family:'Consolas','Monaco',monospace;font-size:0.85rem;overflow-x:auto;}
.diff-line {padding:2px 10px;white-space:pre;}
.diff-line.add {background:#d4edda;color:#155724;}
.diff-line.remove {background:#f8d7da;color:#721c24;}
.diff-line.header {background:#e9ecef;color:#495057;font-weight:600;}
.info-message {padding:20px;background:#d1ecf1;border-left:4px solid:#0c5460;border-radius:6px;color:#0c5460;}
.info-message.error {background:#f8d7da;border-left-color:#721c24;color:#721c24;}
.no-details {padding:15px;color:#6c757d;font-style:italic;text-align:center;}
.dax-full {margin-top:10px;}
.dax-full pre {background:#f8f9fa;padding:12px;border-radius:6px;overflow-x:auto;border-left:3px solid #dee2e6;}
.folder-groups {display:flex;flex-direction:column;gap:20px;}
.folder-group {background:#f8f9fa;border-radius:8px;overflow:hidden;border-left:4px solid #667eea;}
.folder-group.collapsed .folder-body {display:none;}
.folder-group.collapsed .expand-icon {transform:rotate(-90deg);}
.folder-header {padding:15px 20px;background:#f1f3f5;cursor:pointer;user-select:none;display:flex;align-items:center;gap:12px;transition:background 0.2s;}
.folder-header:hover {background:#e9ecef;}
.folder-icon {font-size:1.2rem;}
.folder-name {flex:1;font-size:1.05rem;}
.folder-count {color:#6c757d;font-size:0.9rem;}
.folder-body {padding:15px;}
@media (max-width:1024px) {
.tmdl-split-view {grid-template-columns:1fr;}
.dax-mini-diff {grid-template-columns:1fr;}
.stat-grid {grid-template-columns:repeat(auto-fit,minmax(120px,1fr));}
}
"""
    minified = minify_css(css)
    return f"<style>{minified}</style>"


def get_javascript() -> str:
    """Get minified JavaScript as inline script."""
    js = """
// Tab switching
function switchTab(tabId) {
    document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
    event.target.closest('.tab-button').classList.add('active');
    const pane = document.getElementById(tabId);
    pane.classList.add('active');

    // Lazy load TMDL content if not already loaded
    if (tabId === 'tab-tmdl-full' && pane.dataset.loaded !== 'true') {
        loadTmdlFullView(pane);
    } else if (tabId === 'tab-tmdl-changes' && pane.dataset.loaded !== 'true') {
        loadTmdlChangesView(pane);
    }
}

// Lazy load TMDL full view
function loadTmdlFullView(pane) {
    const data = window.tmdlData;
    if (!data || !data.tmdl1 || !data.tmdl2) {
        pane.innerHTML = '<div class="tmdl-section"><div class="info-message"><p>TMDL data not available.</p></div></div>';
        return;
    }

    pane.innerHTML = '<div class="tmdl-section"><div style="padding:40px;text-align:center;"><div style="font-size:1.2rem;color:#6c757d;">Loading TMDL view...</div></div></div>';

    setTimeout(() => {
        try {
            const html = generateTmdlFullView(data.tmdl1, data.tmdl2, data.model1Name, data.model2Name);
            pane.innerHTML = html;
            pane.dataset.loaded = 'true';
            initTmdlEventListeners();
        } catch (e) {
            pane.innerHTML = '<div class="tmdl-section"><div class="info-message error"><p>Error loading TMDL view: ' + e.message + '</p></div></div>';
        }
    }, 10);
}

// Lazy load TMDL changes view
function loadTmdlChangesView(pane) {
    const data = window.tmdlData;
    if (!data || !data.tmdl1 || !data.tmdl2) {
        pane.innerHTML = '<div class="tmdl-section"><div class="info-message"><p>TMDL data not available.</p></div></div>';
        return;
    }

    pane.innerHTML = '<div class="tmdl-section"><div style="padding:40px;text-align:center;"><div style="font-size:1.2rem;color:#6c757d;">Analyzing changes...</div></div></div>';

    setTimeout(() => {
        try {
            const html = generateTmdlChangesView(data.tmdl1, data.tmdl2);
            pane.innerHTML = html;
            pane.dataset.loaded = 'true';
        } catch (e) {
            pane.innerHTML = '<div class="tmdl-section"><div class="info-message error"><p>Error loading changes: ' + e.message + '</p></div></div>';
        }
    }, 10);
}

// Generate TMDL full view HTML
function generateTmdlFullView(tmdl1, tmdl2, model1Name, model2Name) {
    const lines1 = tmdl1.split('\\n');
    const lines2 = tmdl2.split('\\n');

    const left = buildTmdlLines(lines1, 'model1');
    const right = buildTmdlLines(lines2, 'model2');

    return `
        <div class="tmdl-section">
            <div class="tmdl-controls">
                <label><input type="checkbox" id="sync-scroll" checked> Sync Scroll</label>
            </div>
            <div class="tmdl-split-view">
                <div class="tmdl-pane left">
                    <div class="tmdl-pane-header">${escapeHtml(model1Name)}</div>
                    <div class="tmdl-code-container" id="tmdl-left" data-model="model1">${left}</div>
                </div>
                <div class="tmdl-pane right">
                    <div class="tmdl-pane-header">${escapeHtml(model2Name)}</div>
                    <div class="tmdl-code-container" id="tmdl-right" data-model="model2">${right}</div>
                </div>
            </div>
        </div>`;
}

// Build TMDL lines with data attributes only (no inline handlers)
function buildTmdlLines(lines, modelId) {
    let html = '<div class="tmdl-code">';
    for (let i = 0; i < lines.length; i++) {
        const lineNum = i + 1;
        const content = lines[i] || '';
        const escapedContent = escapeHtml(content) || '&nbsp;';
        html += `<div class="tmdl-line" data-line="${lineNum}" data-model="${modelId}">`;
        html += `<span class="line-number">${lineNum}</span>`;
        html += `<span class="line-content">${escapedContent}</span>`;
        html += '</div>';
    }
    html += '</div>';
    return html;
}

// Generate TMDL changes view (placeholder - full implementation would need semantic diff)
function generateTmdlChangesView(tmdl1, tmdl2) {
    return '<div class="tmdl-section"><div class="info-message"><p>Changes view loaded successfully. Full semantic diff not yet implemented in client-side JS.</p></div></div>';
}

// Event delegation for TMDL interactions
function initTmdlEventListeners() {
    const containers = document.querySelectorAll('.tmdl-code-container');

    // Click handler
    containers.forEach(container => {
        container.addEventListener('click', e => {
            const line = e.target.closest('.tmdl-line');
            if (line) {
                const model = line.dataset.model;
                const lineNum = line.dataset.line;
                onLineClick(model, lineNum);
            }
        });

        // Hover handlers
        container.addEventListener('mouseover', e => {
            const line = e.target.closest('.tmdl-line');
            if (line) {
                line.classList.add('highlight');
            }
        });

        container.addEventListener('mouseout', e => {
            const line = e.target.closest('.tmdl-line');
            if (line) {
                line.classList.remove('highlight');
            }
        });

        // Scroll sync
        container.addEventListener('scroll', e => {
            if (document.getElementById('sync-scroll')?.checked) {
                const source = e.target.dataset.model;
                const target = source === 'model1' ? 'model2' : 'model1';
                const targetContainer = document.querySelector(`[data-model="${target}"]`);
                if (targetContainer && !targetContainer.dataset.syncing) {
                    e.target.dataset.syncing = 'true';
                    targetContainer.scrollTop = e.target.scrollTop;
                    targetContainer.scrollLeft = e.target.scrollLeft;
                    setTimeout(() => delete e.target.dataset.syncing, 50);
                }
            }
        });
    });
}

// Line click handler
function onLineClick(model, lineNum) {
    console.log(`Line clicked: ${model}, line ${lineNum}`);
    document.querySelectorAll('.tmdl-line.highlight').forEach(el => el.classList.remove('highlight'));
    const line = document.querySelector(`.tmdl-line[data-model="${model}"][data-line="${lineNum}"]`);
    if (line) line.classList.add('highlight');
}

// Diff view switcher
function switchDiffView(view) {
    document.querySelectorAll('.toggle-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.diff-view').forEach(v => v.classList.remove('active'));
    document.getElementById('btn-' + view).classList.add('active');
    document.getElementById(view + '-view').classList.add('active');
}

// HTML escape utility
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Model Diff Report initialized');
});
"""
    minified = minify_js(js)
    return f"<script>{minified}</script>"
