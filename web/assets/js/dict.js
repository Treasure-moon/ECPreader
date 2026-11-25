import { api } from './api.js';
import { escapeHtml, toast, toastError } from './ui.js';
import { refreshVocab } from './vocab.js';

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

function bookButtonHtml() {
  return `
    <button class="wp-book" type="button" data-state="empty" aria-label="收藏进生词本" title="收藏进生词本">
      <svg class="bk-star" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3.4l2.6 5.3 5.9.9-4.3 4.1 1 5.9-5.2-2.8-5.2 2.8 1-5.9L3.5 9.6l5.9-.9z"
              fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M9.6 12.1h4.8M12 9.7v4.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
      <span class="bk-ring bk-num">✓</span>
      <span class="bk-ring bk-plus">+1</span>
    </button>`;
}

function senseOf(data) {
  const list = Array.isArray(data.senses) ? data.senses : [];
  return String(data.base_sense || list[0] || data.word_zh || data.word || '').trim();
}

function paintBook(button, info) {
  if (!button) return;
  const count = Number(info?.count) || 0;
  const num = button.querySelector('.bk-num');
  button.classList.remove('is-done', 'is-more', 'is-empty');
  if (!count) {
    button.dataset.state = 'empty';
    button.classList.add('is-empty');
    button.title = '收藏进生词本';
    button.setAttribute('aria-label', '收藏进生词本');
    return;
  }
  if (info.collected) {
    button.dataset.state = 'done';
    button.classList.add('is-done');
    if (num) num.textContent = '✓';
    const places = Number(info.matches?.length) || 1;
    button.title = places > 1 ? `已收藏（${places} 处来源）· 点击取消收藏` : '已收藏 · 点击取消收藏';
    button.setAttribute('aria-label', '已收藏，点击取消');
    return;
  }
  button.dataset.state = 'more';
  button.classList.add('is-more');
  if (num) num.textContent = count > 9 ? '9+' : String(count);
  button.title = `这个词已收藏 ${count} 个语义 · 点击把当前语义也收藏`;
  button.setAttribute('aria-label', `已收藏 ${count} 个语义，点击收藏当前语义`);
}

async function toggleBook(button, data) {
  if (!data?.word) return;
  const sense = senseOf(data);
  const gloss = String(data.context_meaning || '');
  const state = button.dataset.state;
  button.disabled = true;
  try {
    const info =
      state === 'done'
        ? await api.post('/api/vocab/uncollect', { word: data.word, sense, gloss })
        : await api.post('/api/vocab', {
            word: data.word,
            display: data.word,
            sense,
            gloss,
            pos: data.pos || '',
            phonetic: data.phonetic || '',
            senses: Array.isArray(data.senses) ? data.senses : [],
            doc_id: data.docId || '',
            doc_title: data.docTitle || '',
            page: Number.isFinite(Number(data.page)) ? Number(data.page) : null,
            seq: Number.isFinite(Number(data.seq)) ? Number(data.seq) : null,
            sentence: data.sentence_en || '',
            sentence_zh: data.sentence_zh || '',
          });
    paintBook(button, info);
    const label = sense ? `「${data.word}」的「${sense}」` : `「${data.word}」`;
    toast({
      title: state === 'done' ? `已取消收藏${label}` : `已收藏${label}`,
      message: state === 'done' ? '' : '在「⋮ → 生词本」里查看、分类或导出。',
      type: 'success',
      duration: state === 'done' ? 2500 : 4000,
    });
    refreshVocab().catch(() => {});
  } catch (err) {
    toastError(err);
  } finally {
    button.disabled = false;
  }
}

function bindBook(data) {
  const button = popover.querySelector('.wp-book');
  if (!button) return;
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleBook(button, data);
  });
  if (!data?.word) return;
  api
    .post('/api/vocab/status', {
      word: data.word,
      sense: senseOf(data),
      gloss: String(data.context_meaning || ''),
    })
    .then((info) => paintBook(button, info))
    .catch(() => {});
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
      ${bookButtonHtml()}
    </div>
    ${sections.join('')}
    <div class="wp-foot">
      <span>${data.engine && data.engine.startsWith('ai') ? 'AI 语境释义' : '免费词典 / 机翻'}</span>
      <button class="btn small ghost" data-close>关闭</button>
    </div>
  `;
  popover.querySelector('[data-close]')?.addEventListener('click', hideWordPopover);
  bindBook(data);
}

export function showWordPopover({ word, sentence, paragraph, rect, docId, docTitle, page, seq }) {
  // 设置/历史等浮层打开时不再弹出，避免遮挡与误触
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) {
    hideWordPopover();
    return;
  }
  const key = `${word}::${sentence}`;
  if (key === currentKey && !popover.hidden) return;
  currentKey = key;
  const seqNo = ++requestSeq;
  popover.hidden = false;
  popover.innerHTML = loadingHtml(word);
  positionNear(rect);
  api
    .post('/api/dict/lookup', { word, sentence, paragraph })
    .then((data) => {
      if (seqNo !== requestSeq) return;
      render({ ...data, docId, docTitle, page, seq });
      if (rect) positionNear(rect);
    })
    .catch((err) => {
      if (seqNo !== requestSeq) return;
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
