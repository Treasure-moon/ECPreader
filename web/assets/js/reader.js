import { escapeHtml, debounce, formatDate } from './ui.js';

const paper = document.getElementById('paper');
const tocEl = document.getElementById('toc');
const docMetaEl = document.getElementById('docMeta');

let currentDoc = null;
let blockMap = new Map();
let callbacks = {};
let translating = false;

const STATUS_LABEL = {
  parsed: '待翻译',
  translating: '翻译中',
  translated: '已翻译',
  partial: '部分翻译',
};

export function renderDocument(doc, cbs = {}) {
  currentDoc = doc;
  callbacks = cbs;
  blockMap = new Map((doc.blocks || []).map((block) => [block.seq, block]));
  paper.innerHTML = '';
  const inner = document.createElement('div');
  inner.className = 'paper-inner';
  for (const block of doc.blocks || []) inner.appendChild(renderRow(block));
  paper.appendChild(inner);
  renderToc(doc.toc || []);
  renderMeta(doc);
  paper.scrollTop = 0;
  setTranslating(false);
}

export function renderMeta(doc) {
  const filetype = (doc.filetype || '').toUpperCase();
  const pages = doc.page_count ? `${doc.page_count} 页` : '页数未知';
  const blocks = (doc.blocks || []).length;
  const status = STATUS_LABEL[doc.status] || doc.status || '';
  docMetaEl.innerHTML = `
    <div>${escapeHtml(filetype)} · ${escapeHtml(pages)} · ${blocks} 段</div>
    <div>${escapeHtml(status)} · ${escapeHtml(formatDate(doc.created_at))}</div>
  `;
}

export function updateDocStatus(status) {
  if (!currentDoc) return;
  currentDoc.status = status;
  renderMeta(currentDoc);
}

export function getDocument() {
  return currentDoc;
}

export function getBlock(seq) {
  return blockMap.get(seq);
}

export function applyFontSize(size) {
  document.documentElement.style.setProperty('--reading-size', `${size}px`);
}

export function setTranslating(flag) {
  translating = flag;
  paper.querySelectorAll('.tgt-placeholder').forEach((node) => {
    node.classList.toggle('running', flag);
    const label = node.querySelector('.ph-text');
    if (label) label.textContent = flag ? '等待翻译…' : '尚未翻译';
  });
}

export function updateBlockTranslation(seq, translation) {
  const block = blockMap.get(seq);
  if (!block) return;
  block.translation = translation;
  const old = paper.querySelector(`.brow[data-seq="${seq}"]`);
  if (!old) return;
  const fresh = renderRow(block);
  old.replaceWith(fresh);
  if (translating) setTranslating(true);
}

export function scrollToSeq(seq) {
  const row = paper.querySelector(`.brow[data-seq="${seq}"]`);
  if (!row) return;
  row.scrollIntoView({ block: 'start', behavior: 'smooth' });
  row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 1200);
}

export function clearSelectionHighlights() {
  paper.querySelectorAll('.seg.sync, .seg.active').forEach((node) => node.classList.remove('sync', 'active'));
  paper.querySelectorAll('.brow.has-sync').forEach((node) => node.classList.remove('has-sync'));
}

// --------------------------------------------------------------- rendering
function segmentsHtml(segments) {
  return segments
    .map((segment) => `<span class="seg" data-sid="${escapeHtml(segment.sid)}">${escapeHtml(segment.src)}</span>`)
    .join(' ');
}

function translatedSegments(block) {
  const translation = block.translation;
  if (!translation || translation.status !== 'done') return null;
  return translation.segments || {};
}

function tableHtml(block, side) {
  const segments = block.translation?.segments || {};
  const done = block.translation?.status === 'done';
  const rows = block.cells || [];
  const body = rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell) => {
            const value = side === 'src' ? cell.src : done ? segments[cell.sid] || '' : '';
            return `<td><span class="seg" data-sid="${escapeHtml(cell.sid)}">${escapeHtml(value)}</span></td>`;
          })
          .join('')}</tr>`
    )
    .join('');
  return `<table><tbody>${body}</tbody></table>`;
}

function errorHtml(error) {
  const info = error || {};
  return `<div class="block-error">
    <strong>${escapeHtml(info.message || '该段翻译失败')}</strong>
    <span class="hint">${escapeHtml(info.hint || '可以点击下方按钮重试，或在设置中更换翻译引擎。')}</span>
    <br><button class="btn small" data-act="translate">重试此段</button>
  </div>`;
}

function placeholderHtml() {
  return `<div class="tgt-placeholder ${translating ? 'running' : ''}">
    <span class="dot"></span><span class="ph-text">${translating ? '等待翻译…' : '尚未翻译'}</span>
    <button class="link" data-act="translate">翻译此段</button>
  </div>`;
}

function targetHtml(block) {
  const type = block.type;
  if (type === 'formula') {
    const text = (block.segments || []).map((segment) => segment.src).join(' ');
    return `${segmentsHtml(block.segments || [])}<div class="formula-note">公式保留原文，不做翻译</div>`;
  }
  if (type === 'reference') {
    const segments = translatedSegments(block);
    if (segments) {
      const html = (block.segments || [])
        .map((segment) => `<span class="seg" data-sid="${escapeHtml(segment.sid)}">${escapeHtml(segments[segment.sid] || '')}</span>`)
        .join(' ');
      return html;
    }
    return `${segmentsHtml(block.segments || [])}<div class="ref-note">参考文献默认保留原文（可在此段「重译」或设置中开启翻译）</div>`;
  }
  if (type === 'table') {
    if (block.translation?.status === 'done') return tableHtml(block, 'tgt');
    if (block.translation?.status === 'error') return errorHtml(block.translation.error);
    return placeholderHtml();
  }
  if (block.translation?.status === 'error') return errorHtml(block.translation.error);
  const segments = translatedSegments(block);
  if (!segments) return placeholderHtml();
  return (block.segments || [])
    .map((segment) => `<span class="seg" data-sid="${escapeHtml(segment.sid)}">${escapeHtml(segments[segment.sid] || '')}</span>`)
    .join(' ');
}

function renderRow(block) {
  const row = document.createElement('article');
  row.className = `brow type-${block.type} lvl-${block.level || 0}`;
  row.dataset.seq = block.seq;
  row.id = `b${block.seq}`;

  const gutter = document.createElement('div');
  gutter.className = 'gutter';
  const previous = blockMap.get(block.seq - 1);
  if (block.page && (!previous || previous.page !== block.page)) {
    gutter.innerHTML = `<span class="page-badge">p.${block.page}</span>`;
  }
  row.appendChild(gutter);

  const source = document.createElement('div');
  source.className = 'pane src';
  source.innerHTML = block.type === 'table' ? tableHtml(block, 'src') : segmentsHtml(block.segments || []);
  row.appendChild(source);

  const target = document.createElement('div');
  target.className = 'pane tgt';
  target.innerHTML = targetHtml(block);
  row.appendChild(target);

  row.addEventListener('click', () => callbacks.onActiveBlock?.(block.seq));
  return row;
}

export function renderToc(toc, onSelect) {
  tocEl.innerHTML = '';
  if (!toc.length) {
    tocEl.innerHTML = '<div class="toc-empty">没有识别到标题层级</div>';
    return;
  }
  for (const item of toc) {
    const button = document.createElement('button');
    button.className = `toc-item lvl-${Math.min(item.level || 1, 3)}`;
    button.textContent = item.text;
    button.title = item.text;
    button.addEventListener('click', () => (onSelect || scrollToSeq)(item.seq));
    tocEl.appendChild(button);
  }
}

// ---------------------------------------------------------------- copying
async function copyBlock(seq) {
  const block = blockMap.get(seq);
  if (!block) return;
  const source = block.type === 'table'
    ? (block.cells || []).map((row) => row.map((cell) => cell.src).join(' | ')).join('\n')
    : (block.segments || []).map((segment) => segment.src).join(' ');
  const segments = block.translation?.segments || {};
  const target = block.type === 'table'
    ? (block.cells || []).map((row) => row.map((cell) => segments[cell.sid] || '').join(' | ')).join('\n')
    : (block.segments || []).map((segment) => segments[segment.sid] || '').join(' ');
  const text = target ? `${source}\n\n${target}` : source;
  try {
    await navigator.clipboard.writeText(text);
    callbacks.onCopied?.();
  } catch {
    callbacks.onCopyFailed?.();
  }
}

paper.addEventListener('click', (event) => {
  const button = event.target.closest('[data-act]');
  if (!button) return;
  const row = button.closest('.brow');
  if (!row) return;
  const seq = Number(row.dataset.seq);
  const action = button.dataset.act;
  if (action === 'translate') callbacks.onTranslateBlock?.(seq);
  if (action === 'copy') copyBlock(seq);
});

// -------------------------------------------------------------- selection
function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

function handleSelection() {
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) {
    clearSelectionHighlights();
    callbacks.onWordHide?.();
    return;
  }
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    clearSelectionHighlights();
    callbacks.onWordHide?.();
    return;
  }
  const range = selection.getRangeAt(0);
  const startNode = range.startContainer;
  const startElement = startNode.nodeType === 1 ? startNode : startNode.parentElement;
  if (!startElement || !startElement.closest('#paper')) {
    clearSelectionHighlights();
    callbacks.onWordHide?.();
    return;
  }
  const startPane = startElement.closest('.pane');
  if (!startPane) return;
  const side = startPane.classList.contains('src') ? 'src' : 'tgt';
  const rows = [...paper.querySelectorAll('.brow')].filter((row) => {
    try { return range.intersectsNode(row); } catch { return false; }
  });
  if (!rows.length) return;
  const segments = rows
    .flatMap((row) => [...row.querySelectorAll(`.pane.${side} .seg`)])
    .filter((node) => {
      try { return range.intersectsNode(node); } catch { return false; }
    });
  clearSelectionHighlights();
  if (!segments.length) {
    callbacks.onWordHide?.();
    return;
  }
  segments.forEach((node) => node.classList.add('active'));
  rows.forEach((row) => row.classList.add('has-sync'));
  const firstRow = segments[0].closest('.brow');
  if (firstRow) callbacks.onActiveBlock?.(Number(firstRow.dataset.seq));
  const otherSide = side === 'src' ? 'tgt' : 'src';
  const counterparts = [];
  for (const segment of segments) {
    const sid = segment.dataset.sid;
    paper
      .querySelectorAll(`.pane.${otherSide} .seg[data-sid="${cssEscape(sid)}"]`)
      .forEach((node) => {
        node.classList.add('sync');
        counterparts.push(node);
      });
  }
  const paperRect = paper.getBoundingClientRect();
  const outside = counterparts.find((node) => {
    const rect = node.getBoundingClientRect();
    return rect.bottom < paperRect.top + 80 || rect.top > paperRect.bottom - 40;
  });
  if (outside) {
    outside.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  const text = selection.toString().trim();
  if (/^[A-Za-z][A-Za-z'’\-]{1,39}$/.test(text)) {
    const anchor = segments[0];
    const row = anchor.closest('.brow');
    const block = blockMap.get(Number(row?.dataset.seq));
    const sid = anchor.dataset.sid;
    const sentence = side === 'src'
      ? anchor.textContent
      : block?.translation?.segments?.[sid] || '';
    const rect = range.getBoundingClientRect();
    callbacks.onWord?.({
      word: text,
      sentence: sentence || text,
      paragraph: block?.text || '',
      rect,
    });
  } else {
    callbacks.onWordHide?.();
  }
}

document.addEventListener('selectionchange', debounce(handleSelection, 150));

// 选区在弹窗中消失时清理高亮
document.addEventListener('mousedown', (event) => {
  if (event.target.closest('#wordPopover')) return;
  if (!event.target.closest('#paper')) {
    clearSelectionHighlights();
    callbacks.onWordHide?.();
  }
});