// AI 阅读助手：围绕当前论文对话，兼答 ECPreader 自身的使用问题。
// 对话只存在内存里（切走标签、关窗即消失），不写 localStorage、不进历史记录。
import { api } from './api.js';
import { escapeHtml, toast } from './ui.js';

const $ = (id) => document.getElementById(id);

const conversations = new Map(); // docId -> [{role, content}]
const state = {
  enabled: false,
  model: '',
  reason: '',
  open: false,
  doc: null,
  messages: [],
  controller: null,
  streaming: false,
  truncated: false,
  update: null,      // 最新发布版本（服务端缓存，联网查一次）
  updateTried: false,
};

function els() {
  return {
    fab: $('btnChat'),
    panel: $('chatPanel'),
    log: $('chatLog'),
    empty: $('chatEmpty'),
    form: $('chatForm'),
    input: $('chatInput'),
    send: $('chatSend'),
    sub: $('chatSub'),
    head: $('chatPanel')?.querySelector('.chat-head') || null,
  };
}

// ------------------------------------------------------------- 轻量 Markdown
// 只认标题行、无序列表、**粗体** 和 `代码`；其余全部转义，拼回 HTML 里安全。
function renderRich(text) {
  const lines = String(text ?? '').split('\n');
  const out = [];
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    let html = escapeHtml(line)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    if (!line.trim()) {
      out.push('');
      continue;
    }
    if (/^#{1,4}\s+/.test(line)) {
      out.push(`<span class="chat-h">${html.replace(/^#{1,4}\s+/, '')}</span>`);
      continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      out.push(`<span class="chat-li">${html.replace(/^\s*[-*•]\s+/, '')}</span>`);
      continue;
    }
    out.push(html);
  }
  // 连续空行只留一行，免得模型的分段把气泡撑得很散。
  return out.join('\n').replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '');
}

// ------------------------------------------------------------------- 渲染
function nearBottom(log) {
  return log.scrollHeight - log.scrollTop - log.clientHeight < 90;
}

function scrollToBottom(log) {
  log.scrollTop = log.scrollHeight;
}

function messageNode(message) {
  const wrap = document.createElement('div');
  wrap.className = `chat-msg ${message.role}`;
  const role = document.createElement('div');
  role.className = 'chat-role';
  role.textContent = message.role === 'user' ? '你' : 'AI';
  const body = document.createElement('div');
  body.className = 'chat-text';
  body.innerHTML = renderRich(message.content);
  wrap.append(role, body);
  return wrap;
}

function errorNode(error) {
  const box = document.createElement('div');
  box.className = 'chat-error';
  const message = escapeHtml(error?.message || 'AI 回答失败');
  const hint = error?.hint ? `<div class="chat-error-hint">${escapeHtml(error.hint)}</div>` : '';
  box.innerHTML = `${message}${hint}`;
  return box;
}

function noteNode(text) {
  const note = document.createElement('div');
  note.className = 'chat-note';
  note.textContent = text;
  return note;
}

// 上下文超预算时在顶部提示一次：说清包含到第几页、当前这页会不会一起发。
function noteText() {
  const range = state.range;
  if (!state.truncated) return '';
  if (range?.whole) return '';
  const total = range?.total_pages ? `（共 ${range.total_pages} 页）` : '';
  let text = range?.cut_page
    ? `论文较长：只把第 1–${range.cut_page} 页放进了上下文${total}`
    : `论文较长：只把前面一部分放进了上下文${total}`;
  if (range?.focus_page) text += `，另外带上你正在看的第 ${range.focus_page} 页`;
  text += '。跳到想细问的那一页再问，我会把那一页也一起读。';
  return text;
}

function ensureNote() {
  const { log } = els();
  if (!log) return;
  log.querySelector('.chat-note')?.remove();
  const text = noteText();
  if (text && state.messages.length) log.prepend(noteNode(text));
}

function renderLog() {
  const { log, empty } = els();
  if (!log) return;
  const stick = nearBottom(log);
  log.replaceChildren();
  empty.hidden = state.messages.length > 0 || state.streaming;
  if (empty.parentElement !== log) log.appendChild(empty);
  for (const message of state.messages) log.appendChild(messageNode(message));
  ensureNote();
  if (stick || state.streaming) scrollToBottom(log);
}

function setStreaming(on) {
  const { send: sendButton, input } = els();
  state.streaming = on;
  if (sendButton) {
    sendButton.textContent = on ? '停止' : '发送';
    sendButton.classList.toggle('primary', !on);
    sendButton.classList.toggle('ghost', on);
  }
  if (input) input.disabled = false;
}

function showEmpty(on) {
  const { empty } = els();
  if (empty) empty.hidden = !on;
}

// --------------------------------------------------------------- 打开/关闭
function canUse() {
  return Boolean(state.enabled && state.doc);
}

function syncChrome() {
  const fab = $('btnChat');
  if (fab) {
    fab.hidden = !canUse();
    fab.classList.toggle('active', state.open);
  }
  syncPanel();
}

function syncPanel() {
  const { panel } = els();
  if (!panel) return;
  panel.hidden = !(state.open && canUse());
}

export function setStatus(status) {
  state.enabled = Boolean(status?.enabled);
  state.model = String(status?.model || '');
  state.reason = String(status?.reason || '');
  const { sub } = els();
  if (sub) {
    sub.textContent = state.enabled
      ? (state.model || '已配置接口')
      : (state.reason || '尚未配置 AI 接口');
    sub.title = state.enabled
      ? `${state.model || '已配置接口'} · 由你在设置里填写的接口提供`
      : (state.reason || '尚未配置 AI 接口');
  }
  if (!state.enabled) closeChat();
  syncChrome();
}

export function syncDocument(doc) {
  const nextId = doc?.id || null;
  const currentId = state.doc?.id || null;
  if (nextId !== currentId) {
    if (currentId) conversations.set(currentId, state.messages);
    abort();
    state.doc = doc || null;
    state.messages = nextId ? [...(conversations.get(nextId) || [])] : [];
    state.truncated = false;
  state.range = null;
    renderLog();
    if (!nextId) closeChat();
  } else {
    state.doc = doc || null;
  }
  syncChrome();
}

export function openChat() {
  if (!canUse()) {
    if (state.enabled) toast({ title: '先打开一篇论文', message: 'AI 助手会通读你正在读的论文。', type: 'info', duration: 3200 });
    else toast({ title: 'AI 助手还没有启用', message: state.reason || '在「翻译设置 → AI 引擎」里填写 API Key 后即可使用。', type: 'info', duration: 5000 });
    return;
  }
  state.open = true;
  syncChrome();
  renderLog();
  const { log, input } = els();
  if (log) scrollToBottom(log);
  input?.focus();
  checkUpdates();
}

export function closeChat() {
  state.open = false;
  state.controller?.abort();
  state.controller = null;
  setStreaming(false);
  syncPanel();
}

export function toggleChat() {
  if (state.open) closeChat();
  else openChat();
}

export function isOpen() {
  return state.open;
}

// 用户当前停在哪一页：正文太长时，服务端会把这一页也塞进上下文，
// 于是「跳到第 7 页再问」就真的能回答第 7 页的内容。
function currentPage() {
  const scroller = document.getElementById('paper');
  if (!scroller) return 0;
  const box = scroller.getBoundingClientRect();
  const panes = scroller.querySelectorAll('.page-pane[data-page]');
  if (panes.length) {
    let best = 0;
    let bestArea = 0;
    for (const pane of panes) {
      const rect = pane.getBoundingClientRect();
      const height = Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top);
      const width = Math.min(rect.right, box.right) - Math.max(rect.left, box.left);
      const area = Math.max(0, height) * Math.max(0, width);
      if (area > bestArea) {
        bestArea = area;
        best = Number(pane.dataset.page) || 0;
      }
    }
    if (best) return best;
  }
  const rows = scroller.querySelectorAll('.brow[data-page]');
  if (!rows.length) return 0;
  const line = box.top + 40;
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom >= line && rect.height) return Number(row.dataset.page) || 0;
  }
  return Number(rows[rows.length - 1].dataset.page) || 0;
}

// ------------------------------------------------------------- 版本更新提示
// 只在打开助手时问一次（服务端缓存 6 小时、可关）；查到新版就在面板顶部排一行。
export function setUpdates(info) {
  if (!info || typeof info !== 'object') return;
  state.update = info;
  renderUpdateHint();
}

function renderUpdateHint() {
  const { panel, head } = els();
  if (!panel || !head) return;
  let node = panel.querySelector('.chat-update');
  const info = state.update;
  const show = Boolean(info && info.newer && info.latest && info.enabled !== false);
  if (!show) {
    node?.remove();
    return;
  }
  if (!node) {
    node = document.createElement('div');
    node.className = 'chat-update';
    const text = document.createElement('span');
    text.className = 'chat-update-text';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-update-btn';
    button.textContent = '查看更新';
    button.addEventListener('click', () => {
      api.post('/api/system/open-external', {
        url: 'https://github.com/Treasure-moon/ECPreader/releases/latest',
      }).catch(() => {});
    });
    node.append(text, button);
    head.after(node);
  }
  node.querySelector('.chat-update-text').textContent = `有新版 ${info.latest}，你用的是 ${info.current}`;
}

async function checkUpdates() {
  if (state.updateTried && state.update?.checked) {
    renderUpdateHint();
    return;
  }
  state.updateTried = true;
  try {
    const info = await api.get('/api/updates');
    setUpdates(info);
    // 首次打开时后台才刚开始查，过一会儿再取一次缓存结果。
    if (!info?.checked) {
      setTimeout(async () => {
        try {
          setUpdates(await api.get('/api/updates'));
        } catch {
          /* 查不到就不提示 */
        }
      }, 2600);
    }
  } catch {
    /* 版本检查失败不影响对话 */
  }
}

// ------------------------------------------------------------------- 发送
function abort() {
  state.controller?.abort();
  state.controller = null;
  setStreaming(false);
}

function appendError(error) {
  const { log } = els();
  if (!log) return;
  const node = errorNode(error);
  log.appendChild(node);
  scrollToBottom(log);
}

function readSseBlocks(buffer, onBlock) {
  let rest = buffer;
  let index = rest.indexOf('\n\n');
  while (index >= 0) {
    onBlock(rest.slice(0, index));
    rest = rest.slice(index + 2);
    index = rest.indexOf('\n\n');
  }
  return rest;
}

async function send(text, { full = false } = {}) {
  const doc = state.doc;
  if (!doc || !text.trim() || state.streaming) return;
  state.whole = Boolean(full);
  const trimmed = text.trim().slice(0, 4000);
  state.messages.push({ role: 'user', content: trimmed });
  state.messages.push({ role: 'assistant', content: '' });
  const assistant = state.messages[state.messages.length - 1];
  showEmpty(false);
  renderLog();
  const { log } = els();
  const assistantNodes = log ? log.querySelectorAll('.chat-msg.assistant') : [];
  const node = assistantNodes.length ? assistantNodes[assistantNodes.length - 1] : null;
  if (node) node.classList.add('pending');
  const body = node?.querySelector('.chat-text') || null;

  const controller = new AbortController();
  state.controller = controller;
  setStreaming(true);
  state.truncated = false;
  state.range = null;

  let response;
  try {
    response = await fetch(`/api/documents/${encodeURIComponent(doc.id)}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-ECP-Token': api.token },
      body: JSON.stringify({
        messages: state.messages
          .filter((message) => message.content.trim())
          .slice(-16)
          .map((message) => ({ role: message.role, content: message.content })),
        page: currentPage(),
        full: Boolean(full),
      }),
      signal: controller.signal,
    });
  } catch (err) {
    if (!controller.signal.aborted) {
      finishWithError({ message: '连不上本地服务', hint: '确认 ECPreader 主窗口还在运行，然后重试。' });
    }
    return;
  }

  if (!response.ok) {
    let info = null;
    try {
      const data = await response.json();
      info = typeof data.detail === 'object' ? data.detail : { message: String(data.detail || '') };
    } catch {
      info = null;
    }
    finishWithError(info || { message: `请求失败（${response.status}）`, hint: '' });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let failed = null;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = readSseBlocks(buffer, (block) => {
        for (const line of block.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let event = null;
          try {
            event = JSON.parse(payload);
          } catch {
            continue;
          }
          if (typeof event.delta === 'string') {
            assistant.content += event.delta;
            const stick = log ? nearBottom(log) : true;
            if (body) body.innerHTML = renderRich(assistant.content);
            if (stick && log) scrollToBottom(log);
          } else if (event.start) {
            state.truncated = Boolean(event.truncated);
            state.range = {
              whole: Boolean(event.whole),
              cut_page: Number(event.cut_page) || 0,
              total_pages: Number(event.total_pages) || 0,
              focus_page: Number(event.focus_page) || 0,
            };
            ensureNote();
          } else if (event.error) {
            failed = event.error;
          }
        }
      });
    }
  } catch (err) {
    if (!controller.signal.aborted) {
      failed = failed || { message: '回答中断了', hint: '网络或接口断开了，可以重新发送。' };
    }
  } finally {
    setStreaming(false);
    state.controller = null;
    node?.classList.remove('pending');
  }

  if (controller.signal.aborted && !assistant.content) {
    state.messages.pop();
    renderLog();
    return;
  }
  if (!assistant.content.trim()) {
    state.messages.pop();
    renderLog();
    appendError(failed || { message: 'AI 没有返回内容', hint: '换个问法，或者换一个模型再试。' });
    return;
  }
  if (failed) appendError(failed);
  if (body) body.innerHTML = renderRich(assistant.content);
  ensureNote();
  if (log) scrollToBottom(log);
  const { empty } = els();
  if (empty) empty.hidden = true;
}

function finishWithError(error) {
  setStreaming(false);
  state.controller = null;
  state.messages.pop();
  state.messages.pop();
  renderLog();
  appendError(error);
}

// --------------------------------------------------------------------- 交互
function autosize() {
  const { input } = els();
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}

export function initChat() {
  const { fab, form, input, log, empty } = els();
  if (!fab || !form) return;
  fab.addEventListener('click', () => toggleChat());
  $('btnChatClose')?.addEventListener('click', () => closeChat());
  $('btnChatClear')?.addEventListener('click', () => {
    state.messages = [];
    state.truncated = false;
  state.range = null;
    if (state.doc?.id) conversations.delete(state.doc.id);
    renderLog();
    showEmpty(true);
    input?.focus();
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (state.streaming) {
      state.controller?.abort();
      return;
    }
    const text = input?.value || '';
    if (!text.trim()) return;
    input.value = '';
    autosize();
    send(text);
  });
  input?.addEventListener('input', autosize);
  input?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  empty?.addEventListener('click', (event) => {
    const chip = event.target.closest('.chat-chip');
    if (!chip) return;
    if (!state.streaming) send(chip.dataset.prompt || chip.textContent || '', { full: chip.dataset.full === '1' });
  });
  // Esc 关掉助手：捕获阶段先处理，避免同时退出全屏或关掉别的弹层。
  document.addEventListener(
    'keydown',
    (event) => {
      if (event.key === 'Escape' && state.open) {
        event.stopPropagation();
        closeChat();
      }
    },
    true
  );
  syncChrome();
}