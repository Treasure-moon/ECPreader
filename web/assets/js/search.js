const paper = document.getElementById('paper');
let initialized = false;
let getDocument = () => null;
let onUnavailable = () => {};
let bar = null;
let input = null;
let countEl = null;
let prevButton = null;
let nextButton = null;
let closeButton = null;
let results = [];
let current = -1;
let query = '';
let observer = null;
let queryTimer = 0;
let applyTimer = 0;

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

function haystack(text) {
  return String(text || '').toLocaleLowerCase();
}

function visibleSides() {
  if (!paper.querySelector('.view-layout')) return { src: true, tgt: true };
  return {
    src: !paper.querySelector('.page-pane.src')?.hidden,
    tgt: !paper.querySelector('.page-pane.tgt')?.hidden,
  };
}

function buildResults(rawQuery) {
  const doc = getDocument();
  const needle = haystack(rawQuery.trim());
  if (!doc || !needle) return [];
  const visible = visibleSides();
  const found = [];
  const seen = new Set();

  const add = (block, sid, side, text) => {
    if (!text || !visible[side]) return;
    const hay = haystack(text);
    if (!hay.includes(needle)) return;
    const key = `${block.seq}:${sid}:${side}`;
    if (seen.has(key)) return;
    seen.add(key);
    found.push({
      seq: Number(block.seq),
      sid: String(sid),
      side,
      text: String(text),
      page: Number(block.page) || 1,
    });
  };

  for (const block of doc.blocks || []) {
    if (!block || block.seq === undefined) continue;
    if (block.type === 'table') {
      const translated = block.translation?.status === 'done' ? block.translation.segments || {} : {};
      for (const row of block.cells || []) {
        for (const cell of row || []) {
          if (!cell || !cell.sid) continue;
          add(block, cell.sid, 'src', cell.src);
          add(block, cell.sid, 'tgt', translated[cell.sid]);
        }
      }
      continue;
    }
    const translated = block.translation?.status === 'done' ? block.translation.segments || {} : {};
    for (const segment of block.segments || []) {
      if (!segment || !segment.sid) continue;
      add(block, segment.sid, 'src', segment.src);
      add(block, segment.sid, 'tgt', translated[segment.sid]);
    }
  }
  return found;
}

function nodesFor(result) {
  const seq = Number(result.seq) || 0;
  const sid = cssEscape(result.sid);
  if (paper.querySelector('.view-layout')) {
    if (result.side === 'tgt') {
      return [...paper.querySelectorAll(`.ov-block[data-seq="${seq}"] [data-sid="${sid}"]`)];
    }
    return [...paper.querySelectorAll(`.textlayer .w[data-sid="${sid}"]`)];
  }
  return [
    ...paper.querySelectorAll(
      `.brow[data-seq="${seq}"] .pane.${result.side} .seg[data-sid="${sid}"]`
    ),
  ];
}

function supportsHighlights() {
  return typeof CSS !== 'undefined'
    && Boolean(CSS.highlights)
    && typeof Highlight === 'function';
}

function clearHighlights() {
  if (supportsHighlights()) {
    CSS.highlights.delete('ecp-search');
    CSS.highlights.delete('ecp-search-current');
  }
  // 清理 1.4.1 早期版本留下的整句标记，避免升级后残影。
  paper.querySelectorAll('.search-hit, .search-current').forEach((node) => {
    node.classList.remove('search-hit', 'search-current');
  });
}

function makeTextMap(elements) {
  const entries = [];
  let text = '';
  for (const element of elements) {
    if (!element) continue;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const value = node.nodeValue || '';
      if (value) {
        entries.push({ node, start: text.length });
        text += value;
      }
      node = walker.nextNode();
    }
  }
  return { text, entries };
}

function pointAt(map, offset, isEnd) {
  for (const entry of map.entries) {
    const length = entry.node.nodeValue.length;
    const start = entry.start;
    const stop = start + length;
    if (isEnd) {
      if (offset > start && offset <= stop) {
        return { node: entry.node, offset: offset - start };
      }
    } else if (offset >= start && offset < stop) {
      return { node: entry.node, offset: offset - start };
    }
  }
  return null;
}

function findRangesFor(map, needle) {
  if (!map.text || !needle) return [];
  const lower = map.text.toLocaleLowerCase();
  const ranges = [];
  let from = 0;
  while (from <= lower.length - needle.length) {
    const index = lower.indexOf(needle, from);
    if (index < 0) break;
    const start = pointAt(map, index, false);
    const end = pointAt(map, index + needle.length, true);
    if (start && end) {
      try {
        const range = document.createRange();
        range.setStart(start.node, start.offset);
        range.setEnd(end.node, end.offset);
        if (!range.collapsed) ranges.push(range);
      } catch {
        // 页面切换/节点被移除时忽略这一处，MutationObserver 会重新标记。
      }
    }
    from = index + Math.max(1, needle.length);
  }
  return ranges;
}

function rangesForResult(result) {
  const elements = nodesFor(result);
  if (!elements.length) return [];
  return findRangesFor(makeTextMap(elements), haystack(query));
}

function applyHighlights() {
  if (!bar || bar.hidden) return;
  clearHighlights();
  if (!supportsHighlights()) return;
  const all = [];
  const currentRanges = [];
  results.forEach((result, index) => {
    const ranges = rangesForResult(result);
    all.push(...ranges);
    if (index === current) currentRanges.push(...ranges);
  });
  if (all.length) CSS.highlights.set('ecp-search', new Highlight(...all));
  if (currentRanges.length) {
    CSS.highlights.set('ecp-search-current', new Highlight(...currentRanges));
  }
}

function scheduleApply() {
  clearTimeout(applyTimer);
  applyTimer = setTimeout(applyHighlights, 80);
}

function updateStatus() {
  if (prevButton) prevButton.disabled = results.length === 0;
  if (nextButton) nextButton.disabled = results.length === 0;
  if (!countEl) return;
  if (!query.trim()) {
    countEl.textContent = '';
    return;
  }
  if (!results.length) {
    countEl.textContent = '无结果';
    return;
  }
  const side = results[current]?.side === 'tgt' ? '译文' : '原文';
  countEl.textContent = `${current + 1}/${results.length} · ${side}`;
}

function reveal() {
  const result = results[current];
  if (!result) return;
  const layoutOn = Boolean(paper.querySelector('.view-layout'));
  const nodes = nodesFor(result);

  if (layoutOn) {
    const pair = paper.querySelector(`.page-pair[data-page="${result.page}"]`);
    pair?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    if (nodes[0]) nodes[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
    // 原版视图的原文文字层按需加载；页面滚入后等它渲染完再定位一次。
    for (const delay of [220, 620, 1200]) {
      setTimeout(() => {
        if (bar?.hidden || results[current] !== result) return;
        applyHighlights();
        const fresh = nodesFor(result);
        if (fresh[0]) fresh[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, delay);
    }
  } else {
    const row = nodes[0]?.closest('.brow');
    row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

function runSearch({ reveal: shouldReveal = false } = {}) {
  query = input?.value.trim() || '';
  if (!query) {
    results = [];
    current = -1;
    clearHighlights();
    updateStatus();
    return;
  }
  results = buildResults(query);
  current = results.length ? 0 : -1;
  updateStatus();
  applyHighlights();
  if (shouldReveal && current >= 0) reveal();
}

function move(delta) {
  if (!results.length) return;
  current = (current + delta + results.length) % results.length;
  updateStatus();
  applyHighlights();
  reveal();
}

function ensureObserver() {
  if (observer || !('MutationObserver' in window)) return;
  observer = new MutationObserver(scheduleApply);
  observer.observe(paper, { childList: true, subtree: true });
}

export function openSearch() {
  if (!initialized || !getDocument()) {
    onUnavailable?.();
    return false;
  }
  if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) return false;
  bar.hidden = false;
  ensureObserver();
  input.focus();
  input.select();
  if (input.value.trim()) runSearch({ reveal: true });
  return true;
}

export function closeSearch() {
  if (!initialized) return;
  clearTimeout(queryTimer);
  clearTimeout(applyTimer);
  bar.hidden = true;
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  input.value = '';
  results = [];
  current = -1;
  query = '';
  clearHighlights();
  updateStatus();
}

export function refreshSearch() {
  if (!bar || bar.hidden || !query.trim()) return;
  const previous = current;
  results = buildResults(query);
  current = Math.min(Math.max(previous, 0), results.length - 1);
  if (!results.length) current = -1;
  updateStatus();
  applyHighlights();
}

export function isSearchOpen() {
  return Boolean(bar && !bar.hidden);
}

export function initSearch(options = {}) {
  if (initialized) return;
  initialized = true;
  getDocument = options.getDocument || getDocument;
  onUnavailable = options.onUnavailable || onUnavailable;

  bar = document.getElementById('searchBar');
  input = document.getElementById('searchInput');
  countEl = document.getElementById('searchCount');
  prevButton = document.getElementById('searchPrev');
  nextButton = document.getElementById('searchNext');
  closeButton = document.getElementById('searchClose');
  if (!bar || !input) return;

  input.addEventListener('input', () => {
    clearTimeout(queryTimer);
    queryTimer = setTimeout(() => runSearch({ reveal: true }), 110);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (results.length) move(event.shiftKey ? -1 : 1);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeSearch();
    }
  });
  prevButton?.addEventListener('click', () => move(-1));
  nextButton?.addEventListener('click', () => move(1));
  closeButton?.addEventListener('click', () => closeSearch());

  document.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    if (event.key.toLocaleLowerCase() !== 'f') return;
    if (document.querySelector('.modal:not([hidden]), .drawer:not([hidden])')) return;
    event.preventDefault();
    openSearch();
  });
}