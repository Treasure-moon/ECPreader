import { api } from './api.js';
import { escapeHtml } from './ui.js';

const popover = document.getElementById('wordPopover');
let requestSeq = 0;
let currentKey = '';
let hideTimer = null;

export function isWordPopoverOpen() {
  return !popover.hidden;
}

export function hideWordPopover() {
  popover.hidden = true;
  currentKey = '';
}

function positionNear(rect) {
  const margin = 12;
  const width = popover.offsetWidth || 356;
  let left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
  let top = rect.bottom + 10;
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
  requestAnimationFrame(() => {
    const height = popover.offsetHeight;
    if (top + height > window.innerHeight - margin) {
      const above = rect.top - height - 10;
      popover.style.top = `${Math.max(margin, above)}px`;
    }
  });
}

function loadingHtml(word) {
  return `
    <div class="wp-head"><span class="wp-word">${escapeHtml(word)}</span></div>
    <div class="wp-loading"><span class="spinner" style="width:16px;height:16px;border-width:2px"></span>正在查询词典与语境释义…</div>
  `;
}

function errorHtml(err) {
  return `
    <div class="wp-head"><span class="wp-word">查询失败</span></div>
    <div class="wp-context">${escapeHtml(err.message || '无法获取释义')}</div>
    <div class="wp-warn">${escapeHtml(err.hint || '请检查网络后重试。')}</div>
    <div class="wp-foot"><span></span><button class="btn small ghost" data-close>关闭</button></div>
  `;
}

function render(data) {
  const defs = (data.definitions || [])
    .map((item) => `<li><span class="pos">${escapeHtml(item.pos || '')}</span>${escapeHtml(item.def)}</li>`)
    .join('');
  const meaning = data.context_meaning || data.word_zh || '';
  const sections = [];

  if (meaning) {
    sections.push(`
      <div class="wp-section">
        <div class="wp-label">${data.context_meaning ? '语境释义' : '词义'}</div>
        <div class="wp-meaning">${escapeHtml(meaning)}</div>
      </div>`);
  }
  if (data.word_zh && data.context_meaning && data.word_zh !== data.context_meaning) {
    sections.push(`
      <div class="wp-section">
        <div class="wp-label">单词直译</div>
        <div class="wp-context">${escapeHtml(data.word_zh)}</div>
      </div>`);
  }
  if (defs) {
    sections.push(`
      <div class="wp-section">
        <div class="wp-label">词典释义</div>
        <ul class="wp-defs">${defs}</ul>
      </div>`);
  }
  if (data.sentence_en) {
    sections.push(`
      <div class="wp-section">
        <div class="wp-label">本句意译</div>
        <div class="wp-sentence"><span class="en">${escapeHtml(data.sentence_en)}</span><br>${escapeHtml(data.sentence_zh || '（暂无）')}</div>
      </div>`);
  }
  const warnings = (data.warnings || []).filter(Boolean);
  if (warnings.length) {
    sections.push(`<div class="wp-warn">${warnings.map(escapeHtml).join('；')}</div>`);
  }

  popover.innerHTML = `
    <div class="wp-head">
      <span class="wp-word">${escapeHtml(data.word)}</span>
      ${data.phonetic ? `<span class="wp-phonetic">${escapeHtml(data.phonetic)}</span>` : ''}
      ${data.pos ? `<span class="wp-pos">${escapeHtml(data.pos)}</span>` : ''}
    </div>
    ${sections.join('')}
    <div class="wp-foot">
      <span>${data.engine && data.engine.startsWith('ai') ? 'AI 语境释义' : '免费词典 / 机翻'}</span>
      <button class="btn small ghost" data-close>关闭</button>
    </div>
  `;
  popover.querySelector('[data-close]')?.addEventListener('click', hideWordPopover);
}

export function showWordPopover({ word, sentence, paragraph, rect }) {
  // 设置/历史等浮层打开时不再弹出，避免遮挡与误触
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) {
    hideWordPopover();
    return;
  }
  const key = `${word}::${sentence}`;
  if (key === currentKey && !popover.hidden) return;
  currentKey = key;
  const seq = ++requestSeq;
  popover.hidden = false;
  popover.innerHTML = loadingHtml(word);
  positionNear(rect);
  api
    .post('/api/dict/lookup', { word, sentence, paragraph })
    .then((data) => {
      if (seq !== requestSeq) return;
      render(data);
      if (rect) positionNear(rect);
    })
    .catch((err) => {
      if (seq !== requestSeq) return;
      popover.innerHTML = errorHtml(err.info || err);
      popover.querySelector('[data-close]')?.addEventListener('click', hideWordPopover);
    });
}

document.addEventListener('click', (event) => {
  if (popover.hidden) return;
  if (popover.contains(event.target)) return;
  if (event.target.closest('#paper')) return;
  hideWordPopover();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') hideWordPopover();
});

window.addEventListener('scroll', (event) => {
  if (popover.hidden) return;
  if (event.target === popover || popover.contains(event.target)) return;
  hideWordPopover();
}, true);

// 原版视图：服务端已按坐标取好词，直接展示结果
export function showLookupLoading(rect) {
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) return;
  requestSeq += 1;
  currentKey = '';
  popover.hidden = false;
  popover.innerHTML = `
    <div class="wp-head"><span class="wp-word">识别中…</span></div>
    <div class="wp-loading"><span class="spinner" style="width:16px;height:16px;border-width:2px"></span>正在识别单词并查询释义…</div>
  `;
  positionNear(rect);
}

export function showLookupData(data, rect) {
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) return;
  requestSeq += 1;
  currentKey = '';
  popover.hidden = false;
  render(data);
  positionNear(rect);
}

export function showLookupError(errInfo, rect) {
  requestSeq += 1;
  currentKey = '';
  popover.hidden = false;
  popover.innerHTML = errorHtml(errInfo || {});
  popover.querySelector('[data-close]')?.addEventListener('click', hideWordPopover);
  positionNear(rect);
}
