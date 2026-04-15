/**
 * <nd-answer> — streaming AI answer Web Component (Shadow DOM)
 * Usage: <nd-answer q="Hoe stemden partijen over woningbouw?" gemeente="rotterdam"></nd-answer>
 * Theming: --nd-answer-bg, --nd-answer-font-size, --nd-answer-color
 */

const ND_ANSWER_STYLE = `
:host {
  display: block;
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: var(--nd-answer-font-size, 1rem);
  line-height: 1.7;
  color: var(--nd-answer-color, #1a1f1e);
}
.nd-answer-wrapper {
  background: var(--nd-answer-bg, #ffffff);
  border: 1px solid #ddd5ca;
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
  box-shadow: 0 2px 4px rgba(4,40,37,0.04), 0 4px 8px rgba(4,40,37,0.06);
}
.nd-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-bottom: 0.75rem;
}
.nd-steps:empty { margin-bottom: 0; }
.nd-step-pill {
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: 9999px;
  background: #f7f4ef;
  color: #5a6664;
  border: 1px solid #ddd5ca;
  transition: background 0.15s, color 0.15s;
}
.nd-step-pill.active {
  background: #042825;
  color: #f7f4ef;
  border-color: #042825;
}
.nd-step-pill.done {
  background: #d4e8e6;
  color: #042825;
  border-color: #d4e8e6;
}
.nd-answer-content p { margin: 0 0 0.75em; }
.nd-answer-content p:last-child { margin-bottom: 0; }
.nd-answer-content h2 {
  font-size: 1.125rem;
  font-weight: 600;
  margin: 1em 0 0.4em;
  color: #042825;
}
.nd-answer-content strong { font-weight: 600; }
.nd-loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #5a6664;
  font-size: 0.875rem;
}
.nd-spinner {
  width: 1rem; height: 1rem;
  border: 2px solid #ddd5ca;
  border-top-color: #042825;
  border-radius: 50%;
  animation: nd-spin 0.7s linear infinite;
}
@keyframes nd-spin { to { transform: rotate(360deg); } }
.nd-error { color: #c23a3a; font-size: 0.875rem; }
`;

function mdToHtml(text) {
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^(?!<h2|<\/p|<p)/, '<p>')
    .replace(/(?<!>)$/, '</p>');
}

class NdAnswer extends HTMLElement {
  connectedCallback() {
    this._retries = 0;
    this._es = null;
    this._buf = '';
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML =
      `<style>${ND_ANSWER_STYLE}</style>` +
      `<div class="nd-answer-wrapper">` +
      `<div class="nd-steps"></div>` +
      `<div class="nd-loading"><div class="nd-spinner"></div><span>Antwoord wordt geladen\u2026</span></div>` +
      `<div class="nd-answer-content" hidden></div>` +
      `</div>`;
    this._stepsEl = this.shadowRoot.querySelector('.nd-steps');
    this._loadingEl = this.shadowRoot.querySelector('.nd-loading');
    this._contentEl = this.shadowRoot.querySelector('.nd-answer-content');
    this._connect();
  }

  disconnectedCallback() {
    this._close();
  }

  _close() {
    if (this._es) { this._es.close(); this._es = null; }
  }

  _connect() {
    const q = this.getAttribute('q') || '';
    if (!q) { this._showError('Geen vraag opgegeven.'); return; }
    const url = '/api/search/stream?q=' + encodeURIComponent(q);
    const es = new EventSource(url);
    this._es = es;

    es.onmessage = (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      const type = data.type;
      if (type === 'status') {
        this._addStep(data.message || '');
      } else if (type === 'chunk' && data.text) {
        if (!this._buf) {
          this._loadingEl.hidden = true;
          this._contentEl.hidden = false;
          this._stepsEl.querySelectorAll('.nd-step-pill').forEach(p => {
            p.classList.remove('active'); p.classList.add('done');
          });
        }
        this._buf += data.text;
        this._contentEl.innerHTML = '<p>' + mdToHtml(this._buf) + '</p>';
      } else if (type === 'done') {
        this._close();
        this._loadingEl.hidden = true;
        this._stepsEl.querySelectorAll('.nd-step-pill.active').forEach(p => {
          p.classList.remove('active'); p.classList.add('done');
        });
        if (data.error) this._showError(data.error);
      } else if (type === 'error') {
        this._close();
        this._showError(data.message || 'Er is een fout opgetreden.');
      }
    };

    es.onerror = () => {
      this._close();
      if (!this._buf && this._retries < 3) {
        this._retries++;
        setTimeout(() => this._connect(), 2000);
      } else if (!this._buf) {
        this._showError('Verbinding verloren na ' + this._retries + ' pogingen.');
      }
    };
  }

  _addStep(msg) {
    const pill = document.createElement('span');
    pill.className = 'nd-step-pill active';
    pill.textContent = msg;
    this._stepsEl.querySelectorAll('.nd-step-pill.active').forEach(p => {
      p.classList.remove('active'); p.classList.add('done');
    });
    this._stepsEl.appendChild(pill);
  }

  _showError(msg) {
    this._loadingEl.hidden = true;
    this._contentEl.hidden = false;
    this._contentEl.innerHTML = '<p class="nd-error">' + msg + '</p>';
  }
}

customElements.define('nd-answer', NdAnswer);
