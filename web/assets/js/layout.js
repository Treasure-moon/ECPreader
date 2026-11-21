import { api } from './api.js';

const paper = document.getElementById('paper');
const TRANSLATABLE = new Set(['title', 'heading', 'paragraph', 'caption', 'list']);

let doc = null;
let pages = [];
let blocksBySeq = new Map();
let blocksByPage = new Map();
let callbacks = {};
let paneMode = 'dual';
let zoom = 1;
let activeSeq = null;
let resizeObserver = null;
let pageObservers = [];

// --------------------------------------------------------------- 页面渲染
function pageUrl(pageNo) {
  return api.downloadUrl(`/api/documents/${doc.id}/pages/${pageNo}.png?dpi=160`);
}

function place(node, bbox, pageMeta) {
  node.style.left = `${(bbox[0] / pageMeta.w) * 100}%`;
  node.style.top = `${(bbox[1] / pageMeta.h) * 100}%`;
  node.style.width = `${((bbox[2] - bbox[0]) / pageMeta.w) * 100}%`;
  node.style.height = `${((bbox[3] - bbox[1]) / pageMeta.h) * 100}%`;
}

function fontSizeCqw(block, pageMeta) {
  const size = Number(block.size) || 10;
  return Math.max(0.55, (size / pageMeta.w) * 100);
}

function makePane(pageMeta, side) {
  const pane = document.createElement('div');
  pane.className = `page-pane ${side}`;
  pane.dataset.page = pageMeta.n;

  const box = document.createElement('div');
  box.className = 'page-box';
  box.style.aspectRatio = `${pageMeta.w} / ${pageMeta.h}`;

  const img = document.createElement('img');
  img.className = 'page-img';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.draggable = false;
  img.alt = `第 ${pageMeta.n} 页`;
  img.src = pageUrl(pageMeta.n);
  img.addEventListener('error', () => {
    img.classList.add('broken');
    const retry = document.createElement('button');
    retry.className = 'btn small page-retry';
    retry.textContent = '重试加载';
    retry.addEventListener('click', () => {
      retry.remove();
      img.classList.remove('broken');
      img.src = `${pageUrl(pageMeta.n)}&r=${Date.now()}`;
    });
    box.appendChild(retry);
  });
  box.appendChild(img);

  if (side === 'src') {
    addTextLayer(box, pageMeta);
  } else {
    addOverlays(box, pageMeta);
  }

  const badge = document.createElement('span');
  badge.className = 'page-badge-corner';
  badge.textContent = `第 ${pageMeta.n} 页`;
  pane.appendChild(badge);
  pane.appendChild(box);
  return pane;
}

function isPageMargin(block, pageMeta) {
  if (!block.bbox) return false;
  return block.bbox[1] < pageMeta.h * 0.055 || block.bbox[3] > pageMeta.h * 0.945;
}

// --------------------------------------------------------------- 可选文本层
function addTextLayer(box, pageMeta) {
  const layer = document.createElement('div');
  layer.className = 'textlayer';
  layer.dataset.page = pageMeta.n;
  box.appendChild(layer);

  const load = async () => {
    if (layer.dataset.ready) return;
    layer.dataset.ready = '1';
    try {
      const data = await api.get(`/api/documents/${doc.id}/pages/${pageMeta.n}/words`);
      if (!box.isConnected) return;
      renderWords(layer, data, pageMeta);
    } catch {
      layer.dataset.failed = '1';
    }
  };

  if (!('IntersectionObserver' in window)) {
    load();
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        load();
      }
    },
    { root: paper, rootMargin: '900px 0px' },
  );
  observer.observe(box);
  pageObservers.push(observer);
}

function renderWords(layer, data, pageMeta) {
  const fragment = document.createDocumentFragment();
  for (const word of data.words || []) {
    if (!word.w) continue;
    const height = Math.max(1, Number(word.y1) - Number(word.y0));
    const span = document.createElement('span');
    span.className = 'w';
    span.textContent = `${word.w} `;
    if (word.sid) span.dataset.sid = word.sid;
    if (word.seq) span.dataset.seq = word.seq;
    span.style.left = `${(Number(word.x0) / data.width) * 100}%`;
    span.style.top = `${(Number(word.y0) / data.height) * 100}%`;
    span.style.width = `${((Number(word.x1) - Number(word.x0)) / data.width) * 100}%`;
    span.style.height = `${(height / data.height) * 100}%`;
    span.style.fontSize = `${(height / data.width) * 100}cqw`;
    fragment.appendChild(span);
  }
  layer.appendChild(fragment);
}

// --------------------------------------------------------------- 译文覆盖
function addOverlays(box, pageMeta) {
  for (const block of blocksByPage.get(pageMeta.n) || []) {
    if (!TRANSLATABLE.has(block.type) || !block.bbox) continue;
    if (isPageMargin(block, pageMeta)) continue;
    if (block.translation?.status !== 'done') continue;
    box.appendChild(overlayNode(block, pageMeta));
  }
}

function overlayNode(block, pageMeta) {
  const node = document.createElement('div');
  node.className = `ov-block type-${block.type}`;
  node.dataset.seq = block.seq;
  place(node, block.bbox, pageMeta);
  node.style.setProperty('--fs', `${fontSizeCqw(block, pageMeta)}cqw`);
  if (block.align === 'center') node.style.textAlign = 'center';
  const segments = block.translation?.segments || {};
  for (const segment of block.segments || []) {
    const text = segments[segment.sid] || '';
    if (!text) continue;
    const span = document.createElement('span');
    span.dataset.sid = segment.sid;
    span.textContent = text;
    node.appendChild(span);
  }
  if (!node.textContent.trim()) node.textContent = translationText(block);
  requestAnimationFrame(() => fitText(node));
  return node;
}

function translationText(block) {
  const segments = block.translation?.segments || {};
  const parts = (block.segments || []).map((segment) => segments[segment.sid] || '');
  return parts.join('').trim();
}

function fitText(node) {
  let factor = 1;
  node.style.setProperty('--fit', '1');
  let guard = 0;
  while (node.scrollHeight > node.clientHeight + 1 && factor > 0.5 && guard < 30) {
    factor *= 0.94;
    node.style.setProperty('--fit', String(factor));
    guard += 1;
  }
}

// --------------------------------------------------------------- 交互
function elementsOf(seq) {
  return [...document.querySelectorAll(`[data-seq="${seq}"]`)];
}

function selectSeq(seq, { scroll = false } = {}) {
  clearSelection();
  if (seq === null || seq === undefined) return;
  activeSeq = seq;
  for (const node of elementsOf(seq)) node.classList.add('active');
  callbacks.onActiveBlock?.(seq);
  if (scroll) {
    const target = elementsOf(seq)[0];
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

export function clearSelection() {
  for (const node of document.querySelectorAll('.w.active, .ov-block.active')) node.classList.remove('active');
  if (activeSeq === null) return;
  activeSeq = null;
}

function pagePoint(box, clientX, clientY) {
  const rect = box.getBoundingClientRect();
  const pageNo = Number(box.closest('.page-pane').dataset.page);
  const pageMeta = pages.find((item) => item.n === pageNo);
  if (!pageMeta) return null;
  return {
    page: pageNo,
    x: ((clientX - rect.left) / rect.width) * pageMeta.w,
    y: ((clientY - rect.top) / rect.height) * pageMeta.h,
    rect,
  };
}

async function lookupAt(box, event) {
  const point = pagePoint(box, event.clientX, event.clientY);
  if (!point) return;
  callbacks.onLookupStart?.(point.rect);
  try {
    const data = await api.post(`/api/documents/${doc.id}/word-at`, {
      page: point.page,
      x: point.x,
      y: point.y,
    });
    callbacks.onLookupDone?.(data, point.rect);
  } catch (err) {
    callbacks.onLookupError?.(err, point.rect);
  }
}

function bindBox(box) {
  box.addEventListener('click', (event) => {
    const node = event.target.closest('[data-seq]');
    if (!node) {
      clearSelection();
      callbacks.onActiveBlock?.(null);
      return;
    }
    selectSeq(Number(node.dataset.seq));
  });
  box.addEventListener('dblclick', (event) => {
    if (!event.target.closest('.w, .ov-block')) return;
    event.preventDefault();
    lookupAt(box, event);
  });
}

// --------------------------------------------------------------- 选区联动
function clearSync() {
  paper.querySelectorAll('.w.sync, .ov-block [data-sid].sync').forEach((node) => {
    node.classList.remove('sync');
  });
}

function debounce(fn, wait) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function handleSelection() {
  if (!doc || !paper.querySelector('.view-layout')) return;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    clearSync();
    return;
  }
  const range = selection.getRangeAt(0);
  const startNode = range.startContainer;
  const startElement = startNode.nodeType === 1 ? startNode : startNode.parentElement;
  const pane = startElement?.closest?.('.page-pane');
  if (!pane || !paper.contains(pane)) return;

  clearSync();
  const isSource = pane.classList.contains('src');
  const sids = new Set();
  let seq = null;

  if (isSource) {
    const words = [...pane.querySelectorAll('.textlayer .w')].filter((node) => {
      try {
        return range.intersectsNode(node);
      } catch {
        return false;
      }
    });
    if (!words.length) return;
    for (const word of words) {
      if (word.dataset.sid) sids.add(word.dataset.sid);
      if (seq === null && word.dataset.seq) seq = Number(word.dataset.seq);
    }
    for (const sid of sids) {
      paper.querySelectorAll(`.ov-block [data-sid="${sid}"]`).forEach((node) => {
        node.classList.add('sync');
      });
    }
  } else {
    const spans = [...pane.querySelectorAll('.ov-block [data-sid]')].filter((node) => {
      try {
        return range.intersectsNode(node);
      } catch {
        return false;
      }
    });
    if (!spans.length) return;
    for (const span of spans) {
      sids.add(span.dataset.sid);
      const block = span.closest('.ov-block');
      if (seq === null && block?.dataset.seq) seq = Number(block.dataset.seq);
    }
    for (const sid of sids) {
      paper.querySelectorAll(`.textlayer .w[data-sid="${sid}"]`).forEach((node) => {
        node.classList.add('sync');
      });
    }
  }

  if (seq !== null) {
    activeSeq = seq;
    for (const node of elementsOf(seq)) node.classList.add('active');
    callbacks.onActiveBlock?.(seq);
  }
}

document.addEventListener('selectionchange', debounce(handleSelection, 160));
document.addEventListener('mousedown', (event) => {
  if (!event.target.closest('#paper')) clearSync();
});

// --------------------------------------------------------------- 尺寸
function applyWidths() {
  const gap = 18;
  const innerWidth = Math.max(320, paper.clientWidth - 44);
  const width = (paneMode === 'dual' ? (innerWidth - gap) / 2 : innerWidth) * zoom;
  paper.querySelectorAll('.page-pane').forEach((pane) => {
    pane.style.width = `${Math.round(width)}px`;
    const hidden = (paneMode === 'src' && pane.classList.contains('tgt')) ||
      (paneMode === 'tgt' && pane.classList.contains('src'));
    pane.hidden = hidden;
  });
  const label = document.getElementById('zoomLabel');
  if (label) label.textContent = `${Math.round(zoom * 100)}%`;
}

export function setZoom(value) {
  zoom = Math.min(2.6, Math.max(0.7, Number(value) || 1));
  applyWidths();
}

export function getZoom() {
  return zoom;
}

export function resetZoom() {
  zoom = 1;
  applyWidths();
}

export function setPaneMode(mode) {
  paneMode = ['dual', 'src', 'tgt'].includes(mode) ? mode : 'dual';
  applyWidths();
}

// --------------------------------------------------------------- 对外
export function renderLayout(docData, cbs = {}) {
  doc = docData;
  callbacks = cbs;
  pages = (docData.pages_meta || []).map((item) => ({ ...item }));
  if (!pages.length && docData.page_count) {
    pages = Array.from({ length: docData.page_count }, (_, index) => ({
      n: index + 1,
      w: 595,
      h: 842,
    }));
  }
  blocksBySeq = new Map((docData.blocks || []).map((block) => [block.seq, block]));
  blocksByPage = new Map();
  for (const block of docData.blocks || []) {
    if (!blocksByPage.has(block.page)) blocksByPage.set(block.page, []);
    blocksByPage.get(block.page).push(block);
  }
  activeSeq = null;
  pageObservers.forEach((observer) => observer.disconnect());
  pageObservers = [];
  paper.innerHTML = '';
  const inner = document.createElement('div');
  inner.className = 'view-layout';
  inner.id = 'layoutView';
  for (const pageMeta of pages) {
    const pair = document.createElement('section');
    pair.className = 'page-pair';
    pair.dataset.page = pageMeta.n;
    const left = makePane(pageMeta, 'src');
    const right = makePane(pageMeta, 'tgt');
    bindBox(left.querySelector('.page-box'));
    bindBox(right.querySelector('.page-box'));
    pair.appendChild(left);
    pair.appendChild(right);
    inner.appendChild(pair);
  }
  paper.appendChild(inner);
  paper.scrollTop = 0;
  paper.scrollLeft = 0;
  applyWidths();
  if (resizeObserver) resizeObserver.disconnect();
  resizeObserver = new ResizeObserver(() => applyWidths());
  resizeObserver.observe(paper);
  callbacks.onActiveBlock?.(null);
}

export function updateBlockTranslation(seq, translation) {
  const block = blocksBySeq.get(seq);
  if (!block) return;
  block.translation = translation;
  const pageMeta = pages.find((item) => item.n === block.page);
  if (!pageMeta) return;
  const pair = paper.querySelector(`.page-pair[data-page="${block.page}"]`);
  const box = pair?.querySelector('.page-pane.tgt .page-box');
  if (!box) return;
  box.querySelectorAll(`[data-seq="${seq}"].ov-block`).forEach((node) => node.remove());
  if (translation?.status === 'done') {
    const node = overlayNode(block, pageMeta);
    if (activeSeq === seq) node.classList.add('active');
    box.appendChild(node);
  }
}

export function setActive(seq) {
  selectSeq(seq);
}

export function getActiveSeq() {
  return activeSeq;
}

export function scrollToSeq(seq) {
  const block = blocksBySeq.get(seq);
  if (!block) return;
  const pair = paper.querySelector(`.page-pair[data-page="${block.page}"]`);
  pair?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  selectSeq(seq);
  const node = elementsOf(seq)[0];
  if (node) {
    node.classList.add('flash');
    setTimeout(() => node.classList.remove('flash'), 1200);
  }
}

export function setTranslating() {
  /* 原版视图不需要占位；进度条已经显示状态 */
}

export function updateDocStatus() {
  /* 侧栏状态由 app 统一刷新 */
}

export function destroy() {
  pageObservers.forEach((observer) => observer.disconnect());
  pageObservers = [];
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  paper.innerHTML = '';
}

// 与 reader.js 对齐的接口，便于 app.js 在两种视图间切换
export const renderDocument = renderLayout;

export function clearSelectionHighlights() {
  clearSync();
  clearSelection();
  if (callbacks.onActiveBlock) callbacks.onActiveBlock(null);
}

export function getBlock(seq) {
  return blocksBySeq.get(seq) || null;
}

export function applyFontSize() {
  /* 原版视图按页面缩放，不单独调整正文字号 */
}

export function getDocument() {
  return doc;
}