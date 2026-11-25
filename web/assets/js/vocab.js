import { api } from './api.js';
import {
  escapeHtml,
  download,
  toast,
  toastError,
  confirmAction,
  debounce,
} from './ui.js';

const drawer = document.getElementById('vocabDrawer');
const listEl = document.getElementById('vocabList');
const searchEl = document.getElementById('vocabSearch');
const countEl = document.getElementById('vocabCount');
const groupEl = document.getElementById('vocabGroup');
const dropsEl = document.getElementById('vocabDrops');
const tipEl = document.getElementById('vocabTip');
const catEditor = document.getElementById('vocabCatEditor');
const catListEl = document.getElementById('vocabCatList');
const catInput = document.getElementById('vocabCatInput');

const NOISE = /[\s，,、；;。.：:·\-—_()（）[\]【】"'“”‘’]+/g;
const norm = (text) => String(text || '').replace(NOISE, '').toLowerCase();

let data = { words: [], categories: [], total_words: 0, total_entries: 0, total_senses: 0 };
let group = 'word';
let query = '';
let detailWord = '';
let onOpenDoc = () => {};
let drag = null;
let docEntries = new Map();

// ------------------------------------------------------------------ 工具
function dayLabel(stamp) {
  const day = String(stamp || '').slice(0, 10);
  const today = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  const now = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  if (!day) return '未知时间';
  if (day === now) return '今天';
  const yesterday = new Date(today.getTime() - 86400000);
  const ymd = `${yesterday.getFullYear()}-${pad(yesterday.getMonth() + 1)}-${pad(yesterday.getDate())}`;
  if (day === ymd) return '昨天';
  return day;
}

function entryMeta(entry) {
  const title = entry.doc_title || '（没有记录来源）';
  const page = entry.page ? ` · 第 ${entry.page} 页` : '';
  const when = entry.created_at || '';
  return `${title}${page}${when ? ` · 收藏于 ${when}` : ''}`;
}

function categoryOptions(selected) {
  const items = data.categories
    .map(
      (cat) =>
        `<option value="${escapeHtml(cat.id)}"${cat.id === selected ? ' selected' : ''}>${escapeHtml(cat.name)}</option>`,
    )
    .join('');
  return `<option value=""${selected ? '' : ' selected'}>未分类</option>${items}`;
}

function filterWords() {
  const key = norm(query);
  if (!key) return data.words;
  return data.words.filter((word) => {
    const haystack = [
      word.word,
      word.phonetic,
      ...(word.senses || []),
      ...(word.entries || []).flatMap((entry) => [entry.sense, entry.gloss, entry.doc_title, entry.category_name]),
    ]
      .map(norm)
      .join(' ');
    return haystack.includes(key);
  });
}

function wordByName(name) {
  const key = String(name || '').toLowerCase();
  return data.words.find((item) => item.word === key) || null;
}

// ------------------------------------------------------------------ 渲染
function senseRow(entry) {
  const meta = entryMeta(entry);
  return `
    <div class="vsense vsense-kept${entry.alive ? '' : ' is-gone'}" data-entry="${escapeHtml(entry.id)}"
         data-doc="${escapeHtml(entry.doc_id || '')}" data-meta="${escapeHtml(meta)}">
      <span class="vsense-term" role="button" tabindex="0" title="查看这个词的详情">${escapeHtml(entry.sense || '（未记义项）')}</span>
      ${entry.gloss && norm(entry.gloss) !== norm(entry.sense) ? `<span class="vsense-gloss">${escapeHtml(entry.gloss)}</span>` : ''}
      <span class="vsense-where">${escapeHtml(entry.doc_title || '来源丢失')}${entry.alive ? '' : '（文章丢失）'}</span>
      <span class="vsense-tools">
        <select class="vsense-cat" title="移到分类">${categoryOptions(entry.category_id)}</select>
        <button type="button" class="vsense-del" title="删除这条收藏" aria-label="删除">✕</button>
      </span>
    </div>`;
}

function wordCard(word) {
  const order = [...(word.senses || [])];
  for (const entry of word.entries || []) {
    const sense = String(entry.sense || '').trim();
    if (sense && !order.some((item) => norm(item) === norm(sense))) order.push(sense);
  }
  const rows = [];
  for (const sense of order) {
    const matched = (word.entries || []).filter((entry) => norm(entry.sense) === norm(sense));
    if (matched.length) {
      matched.forEach((entry) => rows.push(senseRow(entry)));
    } else {
      rows.push(`
        <div class="vsense vsense-quiet">
          <span class="vsense-term quiet">${escapeHtml(sense)}</span>
          <span class="vsense-note">未收藏</span>
        </div>`);
    }
  }
  if (!rows.length) {
    rows.push(`<div class="vsense vsense-quiet"><span class="vsense-term quiet">${escapeHtml(word.word)}</span></div>`);
  }
  const senses = (word.entries || []).length;
  return `
    <article class="vocab-word" data-word="${escapeHtml(word.word)}">
      <header class="vw-head" role="button" tabindex="0" title="查看这个词的详情">
        <span class="vw-word">${escapeHtml(word.display || word.word)}</span>
        ${word.phonetic ? `<span class="vw-phon">${escapeHtml(word.phonetic)}</span>` : ''}
        ${word.pos ? `<span class="vw-pos">${escapeHtml(word.pos)}</span>` : ''}
        <span class="vw-count">${senses} 个收藏</span>
      </header>
      <div class="vw-senses">${rows.join('')}</div>
    </article>`;
}

function rowHtml(mode, word, entry) {
  const showGloss = entry.gloss && norm(entry.gloss) !== norm(entry.sense);
  return `
    <article class="vocab-row" data-word="${escapeHtml(word.word)}" data-entry="${escapeHtml(entry.id)}" draggable="true">
      <div class="vrow-main">
        <span class="vrow-word">${escapeHtml(word.display || word.word)}</span>
        <span class="vrow-sense">${escapeHtml(entry.sense || '')}</span>
        ${showGloss ? `<span class="vrow-gloss">${escapeHtml(entry.gloss)}</span>` : ''}
      </div>
      <div class="vrow-side">${escapeHtml(entryMemo(mode, entry))}</div>
    </article>`;
}

function entryMemo(mode, entry) {
  if (mode === 'doc') return `${dayLabel(entry.created_at)}${entry.category_name ? ` · ${entry.category_name}` : ''}`;
  if (mode === 'time') return [entry.doc_title || '来源丢失', entry.category_name || '未分类'].join(' · ');
  return [entry.doc_title || '来源丢失', dayLabel(entry.created_at)].join(' · ');
}

function docHeader(key, label, items) {
  const first = items.find((item) => item.entry.doc_id);
  const docId = first ? first.entry.doc_id : '';
  const alive = items.some((item) => item.entry.alive);
  const title = first ? first.entry.doc_title || label : label;
  return `
    <header class="vocab-group-head vg-doc" draggable="true" data-key="${escapeHtml(key)}"
            data-doc="${escapeHtml(docId)}" data-title="${escapeHtml(title)}"
            title="拖到下面的分类上，可把这篇的收藏一次改过去">
      <span class="vg-grip" aria-hidden="true">⠿</span>
      <span class="vg-title" title="${escapeHtml(title)}">${escapeHtml(label)}</span>
      <span class="vg-count">${items.length} 条</span>
      <button type="button" class="vg-open" data-act="open-doc" data-doc="${escapeHtml(docId)}"
              ${docId && alive ? '' : 'disabled'} title="${docId && alive ? '在阅读界面打开这篇文章' : '文章已删除，打不开了'}">打开文章</button>
    </header>`;
}

function groupBlocks(mode) {
  const words = filterWords();
  if (mode === 'word') {
    return [{ key: 'all', head: '', html: words.map(wordCard).join('') }];
  }
  const buckets = new Map();
  for (const word of words) {
    for (const entry of word.entries || []) {
      // 按文章分组时用 doc_id：同一篇文章标题可能相同（例如目录里的同名文件）
      const key =
        mode === 'category'
          ? entry.category_id || ''
          : mode === 'time'
            ? dayLabel(entry.created_at)
            : entry.doc_id || `title:${entry.doc_title || '（没有记录来源）'}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push({ word, entry });
    }
  }
  const docLabels = new Map();
  for (const items of buckets.values()) {
    for (const { entry } of items) {
      if (mode !== 'doc') continue;
      const key = entry.doc_id || `title:${entry.doc_title || '（没有记录来源）'}`;
      if (!docLabels.has(key)) {
        docLabels.set(key, (entry.doc_title || '（没有记录来源）') + (entry.alive ? '' : '（文章丢失）'));
      }
    }
  }
  if (mode === 'category') {
    // 空分类也要列出来，否则没地方拖；搜索时只留有命中的分组
    const ids = data.categories.map((cat) => cat.id).filter((id) => !query || buckets.has(id));
    if (!query || buckets.has('')) ids.push('');
    return ids.map((id) => {
      const items = buckets.get(id) || [];
      const cat = data.categories.find((item) => item.id === id);
      const label = cat ? cat.name : '未分类';
      return {
        key: id,
        head: `<h3 class="vocab-group-head">${escapeHtml(label)}<span class="vg-count">${items.length} 条</span></h3>`,
        html: items.length
          ? items.map(({ word, entry }) => rowHtml(mode, word, entry)).join('')
          : '<div class="vocab-group-empty">把单词拖到这里</div>',
      };
    });
  }
  const order = [...buckets.keys()];
  if (mode === 'time') {
    order.sort((a, b) => (a === '今天' ? -1 : b === '今天' ? 1 : String(b).localeCompare(String(a))));
  }
  return order.map((key) => {
    const items = buckets.get(key);
    const label = mode === 'doc' ? docLabels.get(key) || key : key;
    if (mode === 'doc') docEntries.set(key, items.map((item) => item.entry.id));
    return {
      key,
      head: mode === 'doc' ? docHeader(key, label, items) : `<h3 class="vocab-group-head">${escapeHtml(label)}</h3>`,
      html: items.map(({ word, entry }) => rowHtml(mode, word, entry)).join(''),
    };
  });
}

function renderEmpty() {
  listEl.innerHTML = `
    <div class="vocab-empty">
      <div class="vocab-empty-mark">☆＋</div>
      <p>生词本还是空的。</p>
      <p class="hint">阅读时双击单词，在释义浮层右上角点「☆+」收藏。同一个词在不同论文里意思不一样时，可以分别收藏，生词本里会按义项分开列。</p>
    </div>`;
}

function renderDetail() {
  const word = wordByName(detailWord);
  if (!word) {
    detailWord = '';
    render();
    return;
  }
  listEl.innerHTML = `
    <div class="vocab-detail">
      <button type="button" class="btn small ghost vocab-back" data-act="vocab-back">← 返回列表</button>
      ${wordCard(word)}
    </div>`;
}

function renderDrops() {
  if (group !== 'doc' || detailWord || !data.categories.length) {
    dropsEl.hidden = true;
    dropsEl.innerHTML = '';
    return;
  }
  dropsEl.hidden = false;
  dropsEl.innerHTML = `
    <span class="vd-label">拖到分类</span>
    <button type="button" class="vd-chip" data-cat="">未分类</button>
    ${data.categories
      .map((cat) => `<button type="button" class="vd-chip" data-cat="${escapeHtml(cat.id)}">${escapeHtml(cat.name)}</button>`)
      .join('')}`;
}

function renderCategories() {
  const items = data.categories;
  if (!items.length) {
    catListEl.innerHTML = '<div class="vocab-cat-empty">还没有分类。给收藏分组，比如「方法学」「实验」「术语」。</div>';
    return;
  }
  catListEl.innerHTML = items
    .map(
      (cat) => `
      <div class="vocab-cat" data-id="${escapeHtml(cat.id)}">
        <input class="vocab-cat-name" value="${escapeHtml(cat.name)}" maxlength="40" aria-label="分类名">
        <span class="vocab-cat-total">${cat.total || 0} 条</span>
        <button type="button" class="vsense-del" data-act="del-cat" title="删除分类">✕</button>
      </div>`,
    )
    .join('');
}

function render() {
  const words = filterWords();
  countEl.textContent = data.total_entries
    ? `　${data.total_words} 个词 · ${data.total_senses} 个语义`
    : '';
  docEntries = new Map();
  renderDrops();
  renderCategories();
  if (detailWord) {
    renderDetail();
    return;
  }
  if (!data.total_entries) {
    renderEmpty();
    return;
  }
  if (!words.length) {
    listEl.innerHTML = `<div class="vocab-empty"><p>没有匹配「${escapeHtml(query)}」的收藏。</p></div>`;
    return;
  }
  const blocks = groupBlocks(group);
  listEl.innerHTML = blocks
    .map(
      (block) => `
      <section class="vocab-group" data-key="${escapeHtml(block.key)}">
        ${block.head}
        <div class="vocab-group-body">${block.html}</div>
      </section>`,
    )
    .join('');
}

// ------------------------------------------------------------------ 数据
function applyData(next) {
  if (next && Array.isArray(next.words)) data = next;
  render();
}

async function refresh({ keepScroll = true } = {}) {
  const scroll = keepScroll ? listEl.scrollTop : 0;
  try {
    data = await api.get('/api/vocab');
  } catch (err) {
    listEl.innerHTML = `<div class="vocab-empty"><p>生词本读取失败。</p></div>`;
    toastError(err);
    return;
  }
  render();
  listEl.scrollTop = scroll;
}

export async function openVocab({ word = '' } = {}) {
  query = '';
  searchEl.value = '';
  detailWord = word ? norm(word) : '';
  drawer.hidden = false;
  await refresh({ keepScroll: false });
  listEl.scrollTop = 0;
}

export function closeVocab() {
  drawer.hidden = true;
  detailWord = '';
  hideTip();
}

export function refreshVocab() {
  return refresh();
}

export function isVocabOpen() {
  return !drawer.hidden;
}

// ------------------------------------------------------------------ 悬停提示
function showTip(target, text) {
  tipEl.textContent = text;
  tipEl.style.left = '0px';
  tipEl.style.top = '0px';
  tipEl.hidden = false;
  // 先显示再量尺寸：隐藏时 offsetWidth 是 0，会把提示挤出屏幕
  const width = tipEl.offsetWidth || 240;
  const height = tipEl.offsetHeight || 56;
  const rect = target.getBoundingClientRect();
  const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 12);
  let top = rect.bottom + 8;
  if (top + height > window.innerHeight - 8) {
    top = Math.max(8, rect.top - height - 8);
  }
  tipEl.style.left = `${Math.max(8, left)}px`;
  tipEl.style.top = `${top}px`;
}

function hideTip() {
  tipEl.hidden = true;
}

function openDetail(word) {
  const name = wordByName(norm(word));
  if (!name) return;
  hideTip();
  detailWord = name.word;
  render();
  listEl.scrollTop = 0;
}

// ------------------------------------------------------------------ 交互
async function assignDragged(categoryId) {
  if (!drag || !drag.ids.length) return;
  const ids = drag.ids.slice();
  const label = drag.label;
  drag = null;
  dropsEl.querySelectorAll('.vd-chip').forEach((node) => node.classList.remove('is-over'));
  try {
    const result = await api.post('/api/vocab/categories/assign', {
      entry_ids: ids,
      category_id: categoryId || '',
    });
    const name = categoryId
      ? (data.categories.find((cat) => cat.id === categoryId) || {}).name || '分类'
      : '未分类';
    applyData(result);
    toast({ title: `「${label}」已移到${name}`, type: 'success', duration: 2600 });
  } catch (err) {
    toastError(err);
  }
}

function bind() {
  drawer.querySelectorAll('[data-close="vocab"]').forEach((node) => {
    node.addEventListener('click', closeVocab);
  });

  groupEl.addEventListener('click', (event) => {
    const button = event.target.closest('[data-group]');
    if (!button) return;
    group = button.dataset.group;
    detailWord = '';
    groupEl.querySelectorAll('[data-group]').forEach((node) => {
      node.classList.toggle('on', node === button);
    });
    render();
  });

  searchEl.addEventListener('input', debounce(() => {
    query = searchEl.value.trim();
    if (query) detailWord = '';
    render();
  }, 200));

  listEl.addEventListener('mouseenter', (event) => {
    const term = event.target.closest('.vsense-term');
    if (!term) return;
    const row = term.closest('.vsense-kept');
    if (!row) return;
    const meta = row.dataset.meta || '';
    const extra = row.classList.contains('is-gone') ? '\n文章已删除，只剩这条记录' : '';
    tipEl.style.whiteSpace = 'pre-line';
    showTip(term, `${meta}${extra}`);
  }, true);

  listEl.addEventListener('mouseleave', hideTip, true);

  listEl.addEventListener('click', async (event) => {
    if (event.target.closest('[data-act="vocab-back"]')) {
      detailWord = '';
      render();
      return;
    }
    const openDoc = event.target.closest('[data-act="open-doc"]');
    if (openDoc) {
      event.preventDefault();
      const docId = openDoc.dataset.doc;
      if (!docId) return;
      closeVocab();
      onOpenDoc(docId);
      return;
    }
    const delEntry = event.target.closest('.vsense-del');
    if (delEntry) {
      const row = delEntry.closest('.vsense-kept');
      if (!row) return;
      event.stopPropagation();
      if (!confirmAction('删除这条收藏？', '只删这一条，同词的其他语义不受影响。')) return;
      try {
        await api.del(`/api/vocab/${row.dataset.entry}`);
        await refresh();
      } catch (err) {
        toastError(err);
      }
      return;
    }
    if (event.target.closest('.vsense-cat')) return;
    const term = event.target.closest('.vsense-term');
    if (term) {
      const word = term.closest('.vocab-word')?.dataset.word || '';
      if (word && word !== detailWord) openDetail(word);
      return;
    }
    const row = event.target.closest('.vocab-row');
    if (row) {
      openDetail(row.dataset.word);
      return;
    }
    const head = event.target.closest('.vw-head');
    if (head) {
      const word = head.closest('.vocab-word')?.dataset.word || '';
      if (word && word !== detailWord) openDetail(word);
    }
  });

  listEl.addEventListener('change', async (event) => {
    const select = event.target.closest('.vsense-cat');
    if (!select) return;
    const row = select.closest('.vsense-kept');
    if (!row) return;
    const word = row.closest('.vocab-word')?.dataset.word || '';
    try {
      await api.post(`/api/vocab/${row.dataset.entry}/category`, {
        category_id: select.value,
        word,
      });
      await refresh();
    } catch (err) {
      toastError(err);
    }
  });

  listEl.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const term = event.target.closest('.vsense-term');
    const head = event.target.closest('.vw-head');
    const target = term || head;
    if (!target) return;
    event.preventDefault();
    const word = target.closest('.vocab-word')?.dataset.word || '';
    if (word && word !== detailWord) openDetail(word);
  });

  // ---- 拖到分类：标题拖=整篇，行拖=单条
  listEl.addEventListener('dragstart', (event) => {
    const head = event.target.closest('.vg-doc');
    if (head) {
      const key = head.dataset.key;
      drag = { ids: docEntries.get(key) || [], label: head.dataset.title || '这篇文章' };
      head.classList.add('is-dragging');
    } else {
      const row = event.target.closest('.vocab-row');
      if (!row) return;
      const word = row.querySelector('.vrow-word')?.textContent || row.dataset.word;
      drag = { ids: [row.dataset.entry], label: word };
      row.classList.add('is-dragging');
    }
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', drag.label);
    }
  });

  listEl.addEventListener('dragend', () => {
    drag = null;
    listEl.querySelectorAll('.is-dragging').forEach((node) => node.classList.remove('is-dragging'));
    listEl.querySelectorAll('.vocab-group.is-over').forEach((node) => node.classList.remove('is-over'));
    dropsEl.querySelectorAll('.vd-chip').forEach((node) => node.classList.remove('is-over'));
  });

  dropsEl.addEventListener('dragover', (event) => {
    const chip = event.target.closest('.vd-chip');
    if (!chip || !drag) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    dropsEl.querySelectorAll('.vd-chip').forEach((node) => node.classList.toggle('is-over', node === chip));
  });

  dropsEl.addEventListener('dragleave', (event) => {
    if (event.target.closest('.vd-chip')) event.target.closest('.vd-chip')?.classList.remove('is-over');
  });

  dropsEl.addEventListener('drop', (event) => {
    const chip = event.target.closest('.vd-chip');
    if (!chip || !drag) return;
    event.preventDefault();
    assignDragged(chip.dataset.cat || '');
  });

  // 「分类」视图里直接把单词拖到别的分类分组下
  function groupUnder(target) {
    if (group !== 'category' || !drag) return null;
    return target.closest('.vocab-group');
  }

  listEl.addEventListener('dragover', (event) => {
    const section = groupUnder(event.target);
    if (!section) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    listEl.querySelectorAll('.vocab-group').forEach((node) => {
      node.classList.toggle('is-over', node === section);
    });
  });

  listEl.addEventListener('dragleave', (event) => {
    const section = groupUnder(event.target);
    if (section) section.classList.remove('is-over');
  });

  listEl.addEventListener('drop', (event) => {
    const section = groupUnder(event.target);
    if (!section) return;
    event.preventDefault();
    listEl.querySelectorAll('.vocab-group').forEach((node) => node.classList.remove('is-over'));
    assignDragged(section.dataset.key || '');
  });

  document.getElementById('btnVocabCats').addEventListener('click', () => {
    catEditor.hidden = !catEditor.hidden;
    if (!catEditor.hidden) {
      renderCategories();
      catInput.focus();
    }
  });

  document.getElementById('btnVocabCatAdd').addEventListener('click', async () => {
    const name = catInput.value.trim();
    if (!name) return;
    try {
      await api.post('/api/vocab/categories', { name });
      catInput.value = '';
      await refresh();
    } catch (err) {
      toastError(err);
    }
  });

  catInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') document.getElementById('btnVocabCatAdd').click();
  });

  catListEl.addEventListener('click', async (event) => {
    const del = event.target.closest('[data-act="del-cat"]');
    if (!del) return;
    event.preventDefault();
    const row = del.closest('.vocab-cat');
    if (!row) return;
    if (!confirmAction('删除这个分类？', '分类里的收藏会移回「未分类」，不会丢失。')) return;
    try {
      await api.del(`/api/vocab/categories/${row.dataset.id}`);
      await refresh();
      renderCategories();
      toast({ title: '分类已删除', type: 'success', duration: 2200 });
    } catch (err) {
      toastError(err);
    }
  });

  catListEl.addEventListener('change', async (event) => {
    const input = event.target.closest('.vocab-cat-name');
    if (!input) return;
    const row = input.closest('.vocab-cat');
    if (!row) return;
    try {
      await api.patch(`/api/vocab/categories/${row.dataset.id}`, { name: input.value.trim() });
      await refresh();
    } catch (err) {
      toastError(err);
    }
  });

  document.getElementById('btnVocabExportCsv').addEventListener('click', () => {
    download(api.downloadUrl('/api/vocab/export?fmt=csv'));
  });
  document.getElementById('btnVocabExportMd').addEventListener('click', () => {
    download(api.downloadUrl('/api/vocab/export?fmt=md'));
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !drawer.hidden) {
      event.stopPropagation();
      if (detailWord) {
        detailWord = '';
        render();
      } else {
        closeVocab();
      }
    }
  }, true);
}

export function initVocab({ onOpenDoc: openDoc } = {}) {
  onOpenDoc = openDoc || (() => {});
  bind();
  // 启动时先取一次数据：顶栏书本按钮上有词数提示
  refresh().catch(() => {});
}