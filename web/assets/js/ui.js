const toastRoot = document.getElementById('toasts');
const loadingRoot = document.getElementById('loading');
const loadingText = document.getElementById('loadingText');

export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function debounce(fn, wait = 150) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

export function formatDate(text) {
  if (!text) return '';
  return String(text).replace('T', ' ').slice(0, 16);
}

export function describeError(err) {
  if (!err) return { title: '操作失败', message: '未知错误', hint: '' };
  if (err.code === 'network' || err.status === 0) {
    return {
      title: err.info?.message || '无法连接本地服务',
      message: '本地服务没有响应。',
      hint: '请确认启动 ECPreader 的窗口仍在运行；如果已关闭，请重新双击 start.bat。',
    };
  }
  const info = err.info || {};
  return {
    title: info.message || err.message || '操作失败',
    message: info.message || err.message || '操作失败',
    hint: info.hint || '',
    code: info.code || '',
    detail: info.detail || '',
  };
}

export function toast({ title, message = '', hint = '', type = 'info', duration = 7000, actions = [] }) {
  const node = el('div', `toast ${type}`);
  const actionsHtml = actions.length
    ? `<div class="t-actions">${actions
        .map((action, index) => `<button class="btn small ${action.primary ? 'primary' : 'ghost'}" data-action-index="${index}">${escapeHtml(action.label)}</button>`)
        .join('')}</div>`
    : '';
  node.innerHTML = `
    <div class="t-title"><span>${escapeHtml(title)}</span><button class="t-close" aria-label="关闭">✕</button></div>
    ${message ? `<div class="t-body">${escapeHtml(message)}</div>` : ''}
    ${hint ? `<div class="t-body" style="opacity:.78;margin-top:4px">${escapeHtml(hint)}</div>` : ''}
    ${actionsHtml}
  `;
  node.querySelector('.t-close').addEventListener('click', () => node.remove());
  node.querySelectorAll('[data-action-index]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = actions[Number(button.dataset.actionIndex)];
      node.remove();
      action?.onClick?.();
    });
  });
  toastRoot.appendChild(node);
  if (duration > 0) setTimeout(() => node.remove(), duration);
  return node;
}

export function toastError(err, { title = '出错了', actions = [] } = {}) {
  const info = describeError(err);
  return toast({ title: info.title || title, message: info.message, hint: info.hint, type: 'error', actions, duration: 12000 });
}

let loadingCount = 0;

export function showLoading(text = '处理中…') {
  loadingCount += 1;
  loadingText.textContent = text;
  loadingRoot.hidden = false;
}

export function hideLoading() {
  loadingCount = Math.max(0, loadingCount - 1);
  if (loadingCount === 0) loadingRoot.hidden = true;
}

export function setLoadingText(text) {
  loadingText.textContent = text;
}

export function openModal(node) {
  node.hidden = false;
  document.body.style.overflow = 'hidden';
}

export function closeModal(node) {
  node.hidden = true;
  if (!document.querySelector('.modal:not([hidden])') && !document.querySelector('.drawer:not([hidden])')) {
    document.body.style.overflow = '';
  }
}

export function confirmAction(message, detail = '') {
  return window.confirm(detail ? `${message}\n\n${detail}` : message);
}

export function download(url) {
  const frame = document.createElement('iframe');
  frame.style.display = 'none';
  frame.src = url;
  document.body.appendChild(frame);
  setTimeout(() => frame.remove(), 60000);
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme || 'light';
}