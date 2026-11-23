import { api } from './api.js';
import * as reader from './reader.js';
import * as layout from './layout.js';
import * as dict from './dict.js';
import { initSettings, openSettings } from './settings.js';
import { initHistory, openHistory } from './history.js';
import * as search from './search.js';
import {
  toast,
  toastError,
  escapeHtml,
  showLoading,
  hideLoading,
  applyTheme,
  openModal,
  closeModal,
  download,
  formatDate,
} from './ui.js';

const $ = (id) => document.getElementById(id);
const MODE_LABEL = { free: '传统机翻', ai: 'AI 翻译' };
const HOME_TITLE = '首页';
const APP_TITLE = 'ECPreader · 论文双语对照阅读器';
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

// 旧版解析器把 PDF 元数据里的 XML 实体原样存进了标题（如 &#x2013;），
// 显示前统一还原一次；仍会经过 escapeHtml，不会引入 HTML。
function decodeEntities(value) {
  return String(value ?? '')
    .replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (match, body) => {
      if (body[0] === '#') {
        const code = body[1].toLowerCase() === 'x'
          ? Number.parseInt(body.slice(2), 16)
          : Number.parseInt(body.slice(1), 10);
        try {
          return String.fromCodePoint(code);
        } catch {
          return match;
        }
      }
      return ENTITIES[body.toLowerCase()] ?? match;
    })
    .replace(/\s+/g, ' ')
    .trim();
}

// --------------------------------------------------------------- 标签页
// 每个标签页各自持有文档、视图模式、滚动位置和翻译任务，
// 因此可以同时翻译、阅读多篇论文。
const tabs = [];
let activeTabId = null;
let tabCounter = 0;
let activeView = reader;

function currentTab() {
  return tabs.find((tab) => tab.id === activeTabId) || null;
}

// 其余逻辑仍然只关心「当前标签页」，用访问器把两者接起来。
const state = {
  settings: null,
  engine: null,
  fontSize: 16,
  reparsed: new Set(),
  get doc() { return currentTab()?.doc || null; },
  set doc(value) { const tab = currentTab(); if (tab) tab.doc = value; },
  get jobId() { return currentTab()?.jobId || null; },
  set jobId(value) { const tab = currentTab(); if (tab) tab.jobId = value; },
  get pollTimer() { return currentTab()?.pollTimer || null; },
  set pollTimer(value) { const tab = currentTab(); if (tab) tab.pollTimer = value; },
  get cursor() { return currentTab()?.cursor ?? 0; },
  set cursor(value) { const tab = currentTab(); if (tab) tab.cursor = value; },
  get viewMode() { return currentTab()?.viewMode || 'layout'; },
  set viewMode(value) { const tab = currentTab(); if (tab) tab.viewMode = value; },
  get paneMode() { return currentTab()?.paneMode || 'dual'; },
  set paneMode(value) { const tab = currentTab(); if (tab) tab.paneMode = value; },
  get activeSeq() { return currentTab()?.activeSeq ?? null; },
  set activeSeq(value) { const tab = currentTab(); if (tab) tab.activeSeq = value; },
};

function updateBadge(data) {
  const engine = data.engine || {};
  const badge = $('engineBadge');
  badge.textContent = engine.detail ? `${engine.label} · ${engine.detail}` : engine.label || '未配置';
  badge.classList.toggle('is-warn', !engine.ready);
  badge.title = engine.ready ? '翻译引擎已就绪，点击修改设置' : '引擎尚未就绪，点击配置';
}

async function refreshSettings() {
  const data = await api.get('/api/settings');
  state.settings = data.settings;
  state.engine = data.engine;
  updateBadge(data);
  $('engineSelect').value = data.settings.engine_mode || 'free';
  applyTheme(data.settings.theme || 'light');
  setFontSize(Number(data.settings.font_size) || 16, false);
  return data;
}

// --------------------------------------------------------------- 标签页渲染
function tabTitleOf(doc) {
  const title = decodeEntities(doc?.title || doc?.filename || '');
  return title || '未命名文档';
}

function tabStatus(tab) {
  if (tab.jobId) return 'translating';
  if (tab.doc?.status === 'translated') return 'done';
  if (tab.doc?.status === 'partial') return 'partial';
  return '';
}

function renderTabs() {
  const list = $('tabList');
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const tab of tabs) {
    const node = document.createElement('div');
    node.className = `doc-tab${tab.id === activeTabId ? ' active' : ''}`;
    node.dataset.tabId = tab.id;
    node.setAttribute('role', 'tab');
    node.setAttribute('aria-selected', String(tab.id === activeTabId));
    node.title = tab.doc ? tabTitleOf(tab.doc) : HOME_TITLE;
    node.innerHTML = `
      <span class="doc-tab-dot ${tabStatus(tab)}"></span>
      <span class="doc-tab-title">${escapeHtml(tab.title)}</span>
      <button class="doc-tab-close" type="button" title="关闭标签页（Alt+W）" aria-label="关闭标签页">✕</button>
    `;
    node.addEventListener('click', (event) => {
      if (event.target.closest('.doc-tab-close')) {
        event.stopPropagation();
        closeTab(tab.id);
        return;
      }
      activateTab(tab.id);
    });
    fragment.appendChild(node);
  }
  list.replaceChildren(fragment);
  const active = list.querySelector('.doc-tab.active');
  if (active) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  const tab = currentTab();
  document.title = tab?.doc ? `${tab.title} · ECPreader` : APP_TITLE;
}

function createTab(doc = null, { activate = true } = {}) {
  const tab = {
    id: `tab-${++tabCounter}`,
    doc,
    title: doc ? tabTitleOf(doc) : HOME_TITLE,
    viewMode: localStorage.getItem('ecp.viewMode') === 'flow' ? 'flow' : 'layout',
    paneMode: 'dual',
    activeSeq: null,
    jobId: null,
    pollTimer: null,
    cursor: 0,
    scrollTop: 0,
    lastJob: null,
  };
  tabs.push(tab);
  if (activate) activateTab(tab.id);
  else renderTabs();
  return tab;
}

function presentTab(tab) {
  search.closeSearch();
  dict.hideWordPopover();
  closeMoreMenu();
  closeDocMenu();
  closeExportMenu();
  if (tab.doc) {
    showReader();
    renderCurrent({ scrollTop: tab.scrollTop });
    syncProgress(tab);
  } else {
    showHome();
    hideProgress();
  }
}

function activateTab(id) {
  const tab = tabs.find((item) => item.id === id);
  if (!tab || tab.id === activeTabId) return;
  const previous = currentTab();
  if (previous) previous.scrollTop = $('paper').scrollTop;
  activeTabId = tab.id;
  renderTabs();
  presentTab(tab);
}

function closeTab(id) {
  const index = tabs.findIndex((tab) => tab.id === id);
  if (index < 0) return;
  const [closed] = tabs.splice(index, 1);
  clearTimeout(closed.pollTimer);
  if (!tabs.length) {
    activeTabId = null;
    createTab();
    return;
  }
  if (closed.id === activeTabId) {
    activeTabId = null;
    const next = tabs[Math.min(index, tabs.length - 1)];
    activateTab(next.id);
  } else {
    renderTabs();
  }
}

function openHomeTab() {
  const empty = tabs.find((tab) => !tab.doc);
  if (empty) activateTab(empty.id);
  else createTab();
}

function cycleTab(delta) {
  if (tabs.length < 2) return;
  const index = tabs.findIndex((tab) => tab.id === activeTabId);
  const next = tabs[(index + delta + tabs.length) % tabs.length];
  if (next) activateTab(next.id);
}

// --------------------------------------------------------------- 视图切换
function supportsLayout(doc) {
  return Boolean(doc && doc.layout === 'abs' && (doc.pages_meta || []).length);
}

function useLayout() {
  return state.viewMode === 'layout' && supportsLayout(state.doc);
}

function updateViewChrome() {
  const layoutOn = useLayout();
  $('colhead').hidden = layoutOn;
  $('zoomCtl').hidden = !layoutOn;
  $('paneSwitch').hidden = !layoutOn;
  const layoutButton = $('viewSwitch').querySelector('[data-view="layout"]');
  if (layoutButton) {
    const ok = supportsLayout(state.doc);
    layoutButton.disabled = !ok;
    layoutButton.title = ok
      ? '按 PDF 原始排版显示，图片与公式保持原样'
      : '该文档暂不支持原版视图（Word 文档请用逐句对照）';
  }
  $('viewSwitch').querySelectorAll('[data-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === state.viewMode);
  });
  $('paneSwitch').querySelectorAll('[data-pane]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pane === state.paneMode);
  });
}

function renderCurrent({ preserveScroll = false, scrollTop = 0 } = {}) {
  const doc = state.doc;
  if (!doc) return;
  // 视图切换会重建 DOM；旧查找高亮/结果不再有效。
  search.closeSearch();
  if (state.viewMode === 'layout' && !supportsLayout(doc)) state.viewMode = 'flow';
  const paper = $('paper');
  const ratio = preserveScroll && paper.scrollHeight
    ? paper.scrollTop / paper.scrollHeight
    : 0;
  const layoutOn = useLayout();
  updateViewChrome();
  activeView = layoutOn ? layout : reader;
  if (layoutOn) {
    layout.setPaneMode(state.paneMode);
    layout.renderDocument(doc, callbacks());
    reader.renderToc(doc.toc || [], (seq) => layout.scrollToSeq(seq));
  } else {
    reader.renderDocument(doc, callbacks());
  }
  reader.renderMeta(doc);
  state.activeSeq = null;
  updateBlockTools();
  if (preserveScroll || scrollTop > 0) {
    requestAnimationFrame(() => {
      // .paper 设了 scroll-behavior: smooth（目录跳转需要），
      // 但切标签/切视图要立刻归位，否则会看到一段莫名其妙的滚动动画。
      const top = preserveScroll ? ratio * paper.scrollHeight : scrollTop;
      if (typeof paper.scrollTo === 'function') {
        paper.scrollTo({ top, left: 0, behavior: 'instant' });
      } else {
        paper.scrollTop = top;
      }
    });
  }
}

// --------------------------------------------------------------- 文档打开
function showReader() {
  $('homeView').hidden = true;
  $('readerView').hidden = false;
  document.body.classList.add('reading');
}

function showHome() {
  search.closeSearch();
  if (document.body.classList.contains('focus-mode')) exitFocusMode();
  $('readerView').hidden = true;
  $('homeView').hidden = false;
  document.body.classList.remove('reading');
  updateBlockTools();
  dict.hideWordPopover();
  renderRecent();
}

function callbacks() {
  return {
    onTranslateBlock: (seq) => retranslateBlock(seq),
    onWord: (info) => dict.showWordPopover(info),
    onWordHide: () => dict.hideWordPopover(),
    onCopied: () => toast({ title: '已复制本段中英对照', type: 'success', duration: 2500 }),
    onCopyFailed: () => toast({ title: '复制失败', message: '浏览器拒绝了剪贴板访问，请手动选择复制。', type: 'warn' }),
    onRetranslate: (seq) => retranslateBlock(seq),
    onUntranslated: (seq) => promptTranslateBlock(seq),
    onLookupStart: (rect) => dict.showLookupLoading(rect),
    onLookupDone: (data, rect) => dict.showLookupData(data, rect),
    onLookupError: (err, rect) => dict.showLookupError(err?.info || err, rect),
    onActiveBlock: (seq) => setActiveBlock(seq),
  };
}

async function ensureLayoutReady(document) {
  if (!document || document.filetype !== 'pdf') return document;
  const outdated = Number(document.meta?.parser_version || 0) < 3;
  if (supportsLayout(document) && !outdated) return document;
  if (state.reparsed.has(document.id)) return document;
  state.reparsed.add(document.id);
  showLoading(outdated ? '正在更新目录与公式识别…' : '正在准备原版视图…');
  try {
    const data = await api.post(`/api/documents/${document.id}/reparse`, {});
    toast({
      title: outdated ? '目录结构已更新' : '已启用原版视图',
      message: outdated
        ? '已重新识别章节标题与公式，原有译文已尽量保留。'
        : '已按论文原本的版面重新排布，图片、公式保持原样，原有译文已尽量保留。',
      type: 'success',
      duration: 5000,
    });
    return data.document;
  } catch (err) {
    toast({
      title: outdated ? '目录更新失败' : '暂时无法启用原版视图',
      message: err.info?.message || '已切换为逐句对照模式。',
      type: 'warn',
      duration: 6000,
    });
    return document;
  } finally {
    hideLoading();
  }
}

async function openDocument(document, { newTab = false } = {}) {
  document = await ensureLayoutReady(document);
  let tab = currentTab();
  // 当前标签页已经有文档时，新文档默认另开一页，保留原来的阅读位置。
  if (newTab || !tab || tab.doc) tab = createTab(null, { activate: false });
  tab.doc = document;
  tab.title = tabTitleOf(document);
  tab.jobId = null;
  tab.pollTimer = null;
  tab.cursor = 0;
  tab.activeSeq = null;
  tab.scrollTop = 0;
  tab.lastJob = null;
  if (tab.viewMode === 'layout' && !supportsLayout(document)) tab.viewMode = 'flow';
  if (tab.id !== activeTabId) {
    const previous = currentTab();
    if (previous) previous.scrollTop = $('paper').scrollTop;
    activeTabId = tab.id;
  }
  renderTabs();
  showReader();
  renderCurrent();
  updateBlockTools();
  renderRecent();
}

async function openDocumentById(docId) {
  showLoading('正在打开文档…');
  try {
    const data = await api.get(`/api/documents/${docId}`);
    hideLoading();
    await openDocument(data.document);
    maybeResumeTranslation();
  } catch (err) {
    hideLoading();
    toastError(err);
  }
}

function maybeResumeTranslation() {
  if (!state.doc) return;
  const pending = countPending(state.doc);
  if (pending > 0 && state.doc.status !== 'translating') {
    toast({
      title: `还有 ${pending} 段未翻译`,
      message: '可以点击顶部「翻译全文」继续，或点击段落上的提示逐段翻译。',
      type: 'info',
      duration: 6000,
      actions: [{ label: '翻译全文', primary: true, onClick: () => startTranslation(false) }],
    });
  }
}

function countPending(document) {
  return (document.blocks || []).filter((block) => {
    if (block.type === 'formula') return false;
    if (block.type === 'reference') return false;
    return block.translation?.status !== 'done';
  }).length;
}

function promptTranslateBlock(seq) {
  toast({
    title: '这一段还没有译文',
    message: '可以单独翻译这一段，或点击顶部翻译全文。',
    type: 'info',
    duration: 5000,
    actions: [{ label: '翻译此段', primary: true, onClick: () => retranslateBlock(seq) }],
  });
}

// ------------------------------------------------------------------ 上传
async function handleFile(file) {
  if (!file) return;
  if (!/\.(pdf|docx)$/i.test(file.name)) {
    toast({
      title: '不支持的文件格式',
      message: '目前支持 PDF 和 Word .docx。旧版 .doc 请先在 Word 中另存为 .docx。',
      type: 'warn',
    });
    return;
  }
  if (file.size > 100 * 1024 * 1024) {
    toast({ title: '文件过大', message: '单文件请控制在 100MB 以内。', type: 'warn' });
    return;
  }
  showLoading(`正在解析《${file.name}》…`);
  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api.upload('/api/documents', form);
    hideLoading();
    await openDocument(data.document);
    autoTranslate();
  } catch (err) {
    hideLoading();
    toastError(err);
  }
}

async function autoTranslate() {
  if (!state.doc) return;
  const pending = countPending(state.doc);
  if (!pending) return;
  await startTranslation(false);
}

// ---------------------------------------------------------------- 翻译任务
function updateProgress(job, tab = currentTab()) {
  if (!tab) return;
  tab.lastJob = job;
  if (tab.id === activeTabId) {
    $('progressWrap').hidden = false;
    $('btnTranslateAll').hidden = true;
    const total = job.total || 1;
    const percent = Math.round(((job.done + job.failed) / total) * 100);
    $('progressBar').style.width = `${percent}%`;
    const failed = job.failed ? ` · 失败 ${job.failed}` : '';
    $('progressText').textContent = `${job.done + job.failed}/${job.total}${failed}`;
    $('progressText').title = `正在翻译 ${job.done + job.failed}/${job.total} 段${failed} · ${MODE_LABEL[job.mode] || job.mode}`;
  }
  // 只更新这个标签页的状态点，避免轮询时重建整排标签打断点击。
  updateTabIndicators(tab);
}

function updateTabIndicators(tab) {
  const node = $('tabList').querySelector(`.doc-tab[data-tab-id="${tab.id}"]`);
  const dot = node?.querySelector('.doc-tab-dot');
  if (dot) dot.className = `doc-tab-dot ${tabStatus(tab)}`;
}

function hideProgress() {
  $('progressWrap').hidden = true;
  $('progressBar').style.width = '0%';
  $('btnTranslateAll').hidden = false;
}

function syncProgress(tab) {
  if (tab.jobId) {
    activeView.setTranslating(true);
    if (tab.lastJob) updateProgress(tab.lastJob, tab);
  } else {
    hideProgress();
  }
}

function applyTranslation(tab, seq, translation) {
  const block = (tab.doc?.blocks || []).find((item) => item.seq === seq);
  if (block) block.translation = translation;
  if (tab.id === activeTabId) activeView.updateBlockTranslation(seq, translation);
}

async function startTranslation(force) {
  const tab = currentTab();
  if (!tab?.doc || tab.jobId) return;
  const mode = $('engineSelect').value || 'free';
  try {
    const data = await api.post(`/api/documents/${tab.doc.id}/translate`, { force, mode });
    tab.jobId = data.job.id;
    tab.cursor = 0;
    activeView.setTranslating(true);
    updateProgress(data.job, tab);
    pollJob(tab.id);
  } catch (err) {
    const actions = [];
    if (err.code === 'config_missing' || err.code === 'unauthorized') {
      actions.push({ label: '打开设置', primary: true, onClick: () => openSettings() });
      actions.push({ label: '改用免费机翻', onClick: () => switchMode('free') });
    }
    toastError(err, { actions });
  }
}

async function pollJob(tabId) {
  const tab = tabs.find((item) => item.id === tabId);
  if (!tab || !tab.jobId) return;
  const jobId = tab.jobId;
  try {
    const data = await api.get(`/api/jobs/${jobId}?cursor=${tab.cursor}`);
    if (tab.jobId !== jobId) return;
    const job = data.job;
    for (const update of job.updates || []) {
      applyTranslation(tab, update.seq, update.translation);
    }
    tab.cursor = job.cursor;
    updateProgress(job, tab);
    if (job.status === 'running') {
      tab.pollTimer = setTimeout(() => pollJob(tab.id), 700);
    } else {
      finishJob(tab, job);
    }
  } catch (err) {
    finishJob(tab, null, err);
  }
}

function finishJob(tab, job, error) {
  clearTimeout(tab.pollTimer);
  tab.pollTimer = null;
  tab.jobId = null;
  tab.lastJob = null;
  const active = tab.id === activeTabId;
  if (active) {
    hideProgress();
    activeView.setTranslating(false);
  }
  if (error) {
    if (active) toastError(error);
    else toast({ title: `《${tab.title}》翻译中断`, message: '可以回到该标签页重新翻译。', type: 'warn', duration: 8000 });
    return;
  }
  if (!job) return;
  const prefix = active ? '' : `《${tab.title}》`;
  if (job.status === 'done') {
    setDocStatus(tab, 'translated');
    toast({
      title: `${prefix}全文翻译完成`,
      message: `共 ${job.total} 段，使用 ${MODE_LABEL[job.mode] || job.mode}。`,
      type: 'success',
      duration: 5000,
    });
  } else if (job.status === 'partial') {
    setDocStatus(tab, 'partial');
    toast({
      title: `${prefix}部分段落翻译失败（${job.failed} 段）`,
      message: job.error?.message || '可以点击失败段落的「重译」，或在设置中更换引擎。',
      hint: job.error?.hint || '',
      type: 'warn',
      duration: 12000,
      actions: [{ label: '打开设置', onClick: () => openSettings() }],
    });
  } else {
    setDocStatus(tab, 'partial');
    const info = job.error || {};
    const freeQuota = info.code === 'free_quota';
    const quota = freeQuota || info.code === 'quota';
    const actions = [{ label: '打开翻译设置', primary: true, onClick: () => openSettings() }];
    if (job.mode === 'free') actions.push({ label: '改用 AI 翻译', onClick: () => switchMode('ai') });
    else actions.push({ label: '改用免费机翻', onClick: () => switchMode('free') });
    toast({
      title: freeQuota ? '免费翻译额度已用完，已停止' : (quota ? '接口额度不足，已停止' : (info.message || '翻译失败')),
      message: quota ? (info.message || '接口额度已用尽，后续请求已停止。') : (info.hint || '请检查翻译引擎设置后重试。'),
      hint: quota ? (info.hint || '请更换引擎或到服务商控制台处理额度。') : '',
      type: 'error',
      duration: 20000,
      actions,
    });
  }
  renderRecent();
}

function setDocStatus(tab, status) {
  if (tab.doc) tab.doc.status = status;
  renderTabs();
  if (tab.id === activeTabId) reader.renderMeta(tab.doc || {});
}

function setActiveBlock(seq) {
  state.activeSeq = (seq === null || seq === undefined || seq === '') ? null : Number(seq);
  updateBlockTools();
}

function updateBlockTools() {
  const has = state.activeSeq !== null && Boolean(state.doc);
  const retranslate = $('btnBlockRetranslate');
  const copy = $('btnBlockCopy');
  if (retranslate) retranslate.disabled = !has;
  if (copy) copy.disabled = !has;
}

function blockPlainText(seq) {
  const block = (state.doc?.blocks || []).find((item) => item.seq === seq);
  if (!block) return '';
  const translated = block.translation?.segments || {};
  if (block.type === 'table') {
    const cells = block.cells || [];
    const source = cells.map((row) => row.map((cell) => cell.src).join(' | ')).join(String.fromCharCode(10));
    const target = cells.map((row) => row.map((cell) => translated[cell.sid] || '').join(' | ')).join(String.fromCharCode(10));
    return target.trim() ? `${source}

${target}` : source;
  }
  const source = (block.segments || []).map((segment) => segment.src).join(' ');
  const target = (block.segments || []).map((segment) => translated[segment.sid] || '').join(' ');
  return target.trim() ? `${source}

${target}` : source;
}

async function copyText(text) {
  if (!text || !text.trim()) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

async function copySelectionOrBlock() {
  const selection = window.getSelection();
  const paper = $('paper');
  const selected = selection && !selection.isCollapsed
    && selection.anchorNode
    && paper.contains(selection.anchorNode)
    ? selection.toString()
    : '';
  const text = selected.trim()
    ? selected
    : (state.activeSeq !== null ? blockPlainText(state.activeSeq) : '');
  if (!text.trim()) {
    toast({ title: '还没有可复制的内容', message: '先选中文字，或在段落上点一下再复制。', type: 'info' });
    return;
  }
  const ok = await copyText(text);
  toast(
    ok
      ? { title: selected.trim() ? '已复制选中内容' : '已复制本段中英对照', type: 'success', duration: 2200 }
      : { title: '复制失败', message: '浏览器拒绝了剪贴板访问，请手动选择后按 Ctrl+C。', type: 'warn' },
  );
}

async function retranslateBlock(seq) {
  const tab = currentTab();
  if (!tab?.doc) return;
  const docId = tab.doc.id;
  try {
    const data = await api.post(`/api/documents/${docId}/blocks/${seq}/translate`, {
      mode: $('engineSelect').value,
    });
    applyTranslation(tab, seq, data.translation);
  } catch (err) {
    applyTranslation(tab, seq, {
      status: 'error',
      segments: {},
      error: { message: err.info?.message || '重译失败', hint: err.info?.hint || '' },
    });
    toastError(err);
  }
}

async function cancelJob() {
  const tab = currentTab();
  if (!tab?.jobId) return;
  try {
    await api.post(`/api/jobs/${tab.jobId}/cancel`, {});
    toast({ title: '已请求取消', message: '正在停止翻译，已完成的段落会保留。', type: 'info' });
  } catch (err) {
    toastError(err);
  }
}

async function switchMode(mode) {
  $('engineSelect').value = mode;
  try {
    await api.put('/api/settings', { settings: { engine_mode: mode } });
    await refreshSettings();
    const pending = countPending(state.doc || { blocks: [] });
    if (pending > 0) {
      toast({
        title: `已切换到${MODE_LABEL[mode]}`,
        message: '是否开始翻译当前文档？',
        type: 'info',
        actions: [{ label: '开始翻译', primary: true, onClick: () => startTranslation(false) }],
      });
    }
  } catch (err) {
    toastError(err);
  }
}

// ---------------------------------------------------------------- 最近文档
async function renderRecent() {
  const container = $('recentList');
  try {
    const data = await api.get('/api/documents?limit=8');
    const documents = data.documents || [];
    if (!documents.length) {
      container.innerHTML = '<div class="recent-empty">还没有文档。拖入第一篇论文开始吧。</div>';
      return;
    }
    container.innerHTML = documents
      .map((doc) => {
        const status = doc.status === 'translated' ? '已翻译' : doc.status === 'partial' ? '部分翻译' : doc.status === 'translating' ? '翻译中' : '待翻译';
        return `<div class="recent-item" data-doc-id="${escapeHtml(doc.id)}">
          <div>
            <div class="r-title">${escapeHtml(decodeEntities(doc.title))}</div>
            <div class="r-meta">${escapeHtml((doc.filetype || '').toUpperCase())} · ${escapeHtml(formatDate(doc.created_at))} · ${escapeHtml(doc.filename || '')}</div>
          </div>
          <span class="r-status">${escapeHtml(status)}</span>
        </div>`;
      })
      .join('');
    container.querySelectorAll('.recent-item').forEach((item) => {
      item.addEventListener('click', () => openDocumentById(item.dataset.docId));
    });
  } catch (err) {
    container.innerHTML = '<div class="recent-empty">读取最近文档失败</div>';
  }
}

// ------------------------------------------------------------------- 导出
let exportMenu = null;

function closeExportMenu() {
  exportMenu?.remove();
  exportMenu = null;
}

function openExportMenu(anchor) {
  closeExportMenu();
  if (!state.doc) return;
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.innerHTML = `
    <button data-format="md">Markdown 对照（.md）</button>
    <button data-format="html">网页（.html）</button>
    <button data-format="json">结构化数据（.json）</button>
    <button data-format="txt">纯英文文本（.txt）</button>
  `;
  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  const top = Math.min(rect.bottom + 8, window.innerHeight - menu.offsetHeight - 12);
  const left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12);
  menu.style.top = `${Math.max(12, top)}px`;
  menu.style.left = `${Math.max(12, left)}px`;
  menu.addEventListener('click', (event) => {
    const button = event.target.closest('[data-format]');
    if (!button) return;
    const format = button.dataset.format;
    download(api.downloadUrl(`/api/documents/${state.doc.id}/export?format=${format}`));
    closeExportMenu();
  });
  exportMenu = menu;
}

// ---------------------------------------------------------------- 弹出菜单
// 第一层「⋮」放全局项（打开、历史、引擎、设置、主题、关于），
// 第二层「⋯」只放当前论文相关项（字号、重译、导出）。
function placeMenu(menu, anchor) {
  const rect = anchor.getBoundingClientRect();
  const left = Math.max(12, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12));
  const top = Math.min(rect.bottom + 8, window.innerHeight - menu.offsetHeight - 12);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function openMoreMenu() {
  closeDocMenu();
  const menu = $('moreMenu');
  menu.hidden = false;
  placeMenu(menu, $('btnMore'));
}

function closeMoreMenu() {
  $('moreMenu').hidden = true;
}

function toggleMoreMenu() {
  if ($('moreMenu').hidden) openMoreMenu();
  else closeMoreMenu();
}

function openDocMenu() {
  closeMoreMenu();
  const menu = $('docMenu');
  const hasDoc = Boolean(state.doc);
  $('btnExport').disabled = !hasDoc;
  $('btnRetranslate').disabled = !hasDoc;
  menu.hidden = false;
  placeMenu(menu, $('btnDocMore'));
}

function closeDocMenu() {
  $('docMenu').hidden = true;
}

function toggleDocMenu() {
  if ($('docMenu').hidden) openDocMenu();
  else closeDocMenu();
}

// ------------------------------------------------------------------- 全屏阅读
let nativeFullscreen = false;
let focusIdleTimer = null;
let lastPointerMove = 0;
let focusSideInert = false;

function isFocusMode() {
  return document.body.classList.contains('focus-mode');
}

function scheduleFocusBarHide() {
  clearTimeout(focusIdleTimer);
  const bar = $('focusBar');
  bar.classList.remove('idle');
  focusIdleTimer = setTimeout(() => {
    if (!bar.matches(':hover')) bar.classList.add('idle');
  }, 2400);
}

function enterFocusMode() {
  if (!state.doc) {
    toast({ title: '先打开一篇论文', message: '全屏阅读只在文档界面可用。', type: 'info', duration: 3000 });
    return;
  }
  if (isFocusMode()) return;
  closeMoreMenu();
  closeDocMenu();
  closeExportMenu();
  document.body.classList.add('focus-mode');
  document.body.classList.remove('toc-open');
  $('focusBar').hidden = false;
  $('focusEdge').hidden = false;
  // 侧栏此前可能被收起（inert），全屏时目录要从左边缘唤出，必须可交互。
  focusSideInert = Boolean($('sidePanel').inert);
  $('sidePanel').inert = false;
  scheduleFocusBarHide();
  // 优先进入系统级全屏；WebView 不支持时退化为铺满窗口的沉浸模式。
  const root = document.documentElement;
  if (!document.fullscreenElement && typeof root.requestFullscreen === 'function') {
    root.requestFullscreen()
      .then(() => { nativeFullscreen = true; })
      .catch(() => { nativeFullscreen = false; });
  }
  requestAnimationFrame(() => {
    if (useLayout()) layout.setZoom(layout.getZoom());
  });
}

function exitFocusMode({ fromFullscreen = false } = {}) {
  if (!isFocusMode()) return;
  document.body.classList.remove('focus-mode', 'toc-open');
  $('focusBar').hidden = true;
  $('focusEdge').hidden = true;
  $('focusBar').classList.remove('idle');
  clearTimeout(focusIdleTimer);
  if (focusSideInert) {
    $('sidePanel').inert = true;
    focusSideInert = false;
  }
  if (!fromFullscreen && nativeFullscreen && document.fullscreenElement && document.exitFullscreen) {
    document.exitFullscreen().catch(() => {});
  }
  nativeFullscreen = false;
}

function toggleFocusMode() {
  if (isFocusMode()) exitFocusMode();
  else enterFocusMode();
}

function toggleFocusToc(open) {
  if (!isFocusMode()) return;
  const next = open === undefined ? !document.body.classList.contains('toc-open') : Boolean(open);
  document.body.classList.toggle('toc-open', next);
  const button = $('btnFocusToc');
  if (button) button.classList.toggle('is-active', next);
}

function isTypingTarget(target) {
  const tag = target?.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || Boolean(target?.isContentEditable);
}

// ------------------------------------------------------------------- 字号
function setFontSize(size, persist = true) {
  const value = Math.min(24, Math.max(13, Number(size) || 16));
  state.fontSize = value;
  reader.applyFontSize(value);
  $('fontLabel').textContent = String(value);
  if (persist) saveFontSizeDebounced(value);
}

const saveFontSizeDebounced = (() => {
  let timer = null;
  return (value) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      api.put('/api/settings', { settings: { font_size: value } }).catch(() => {});
    }, 600);
  };
})();

// ------------------------------------------------------------------- 主题
async function cycleTheme() {
  const order = ['light', 'sepia', 'dark'];
  const current = document.documentElement.dataset.theme || 'light';
  const next = order[(order.indexOf(current) + 1) % order.length];
  applyTheme(next);
  try {
    await api.put('/api/settings', { settings: { theme: next } });
  } catch { /* 静默失败即可 */ }
}

// ------------------------------------------------------------------- 初始化
function setSideCollapsed(collapsed) {
  const node = $('readerView');
  const side = $('sidePanel');
  node.classList.toggle('side-collapsed', collapsed);
  side.setAttribute('aria-hidden', String(collapsed));
  side.inert = collapsed;
  const top = $('btnToggleSideTop');
  top.setAttribute('aria-expanded', String(!collapsed));
  top.title = collapsed ? '展开目录侧栏' : '收起目录侧栏';
  top.classList.toggle('is-active', !collapsed);
}

function toggleSide() {
  if (isFocusMode()) {
    toggleFocusToc();
    return;
  }
  setSideCollapsed(!$('readerView').classList.contains('side-collapsed'));
}

function openAbout() {
  const version = document.querySelector('meta[name="ecp-version"]')?.content || '';
  $('aboutVersion').textContent = version;
  openModal($('aboutModal'));
}

async function openExternal(url) {
  try {
    const result = await api.post('/api/system/open-external', { url });
    if (result?.ok === false) window.open(url, '_blank', 'noopener');
  } catch (err) {
    toastError(err);
  }
}

function bind() {
  $('btnOpen').addEventListener('click', () => {
    closeMoreMenu();
    $('fileInput').click();
  });
  $('btnPick').addEventListener('click', (event) => {
    event.stopPropagation();
    $('fileInput').click();
  });
  $('fileInput').addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) handleFile(file);
  });
  $('btnSettings').addEventListener('click', () => {
    closeMoreMenu();
    openSettings();
  });
  $('engineBadge').addEventListener('click', () => openSettings());
  $('btnHistory').addEventListener('click', () => {
    closeMoreMenu();
    openHistory();
  });
  $('btnHistoryHome').addEventListener('click', () => openHistory());
  $('btnTheme').addEventListener('click', () => {
    closeMoreMenu();
    cycleTheme();
  });
  $('btnSearch').addEventListener('click', () => {
    closeMoreMenu();
    search.openSearch();
  });
  $('btnAboutTop').addEventListener('click', () => {
    closeMoreMenu();
    closeDocMenu();
    openAbout();
  });
  $('btnMore').addEventListener('click', (event) => {
    event.stopPropagation();
    toggleMoreMenu();
  });
  $('btnDocMore').addEventListener('click', (event) => {
    event.stopPropagation();
    toggleDocMenu();
  });
  $('aboutModal').querySelectorAll('[data-close="about"]').forEach((node) => {
    node.addEventListener('click', () => closeModal($('aboutModal')));
  });
  $('aboutModal').querySelectorAll('[data-external]').forEach((node) => {
    node.addEventListener('click', () => openExternal(node.dataset.external));
  });
  $('btnTranslateAll').addEventListener('click', () => startTranslation(false));
  $('btnRetranslate').addEventListener('click', () => {
    closeDocMenu();
    if (window.confirm('重新翻译会覆盖当前译文，确定继续吗？')) startTranslation(true);
  });
  $('btnCancelJob').addEventListener('click', cancelJob);
  $('btnExport').addEventListener('click', (event) => {
    openExportMenu(event.currentTarget);
    closeDocMenu();
  });
  $('btnBlockRetranslate').addEventListener('click', () => {
    if (state.activeSeq !== null) retranslateBlock(state.activeSeq);
  });
  $('btnBlockCopy').addEventListener('click', copySelectionOrBlock);
  $('fontMinus').addEventListener('click', () => setFontSize(state.fontSize - 1));
  $('fontPlus').addEventListener('click', () => setFontSize(state.fontSize + 1));
  $('engineSelect').addEventListener('change', (event) => {
    closeMoreMenu();
    switchMode(event.target.value);
  });
  $('brandHome').addEventListener('click', openHomeTab);
  $('btnNewTab').addEventListener('click', () => createTab());
  $('btnToggleSide').addEventListener('click', toggleSide);
  $('btnToggleSideTop').addEventListener('click', toggleSide);
  $('btnFocus').addEventListener('click', toggleFocusMode);
  $('btnFocusSearch').addEventListener('click', () => search.openSearch());
  $('btnFocusToc').addEventListener('click', () => toggleFocusToc());
  $('btnFocusExit').addEventListener('click', () => exitFocusMode());
  $('focusBar').addEventListener('mouseenter', () => {
    clearTimeout(focusIdleTimer);
    $('focusBar').classList.remove('idle');
  });
  $('focusBar').addEventListener('mouseleave', scheduleFocusBarHide);
  $('focusEdge').addEventListener('mouseenter', () => toggleFocusToc(true));
  // 全屏时选完章节就把抽屉收回，留出完整阅读宽度。
  $('toc').addEventListener('click', (event) => {
    if (isFocusMode() && event.target.closest('.toc-item')) toggleFocusToc(false);
  });

  // 标签页：滚轮横向滚动、中键关闭
  $('tabstrip').addEventListener('wheel', (event) => {
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    event.preventDefault();
    $('tabList').scrollLeft += event.deltaY;
  }, { passive: false });
  $('tabList').addEventListener('auxclick', (event) => {
    if (event.button !== 1) return;
    const node = event.target.closest('.doc-tab');
    if (node) {
      event.preventDefault();
      closeTab(node.dataset.tabId);
    }
  });

  // 视图切换
  $('viewSwitch').addEventListener('click', (event) => {
    const button = event.target.closest('[data-view]');
    if (!button || button.disabled) return;
    state.viewMode = button.dataset.view;
    localStorage.setItem('ecp.viewMode', state.viewMode);
    renderCurrent({ preserveScroll: true });
  });
  $('paneSwitch').addEventListener('click', (event) => {
    const button = event.target.closest('[data-pane]');
    if (!button) return;
    search.closeSearch();
    state.paneMode = button.dataset.pane;
    layout.setPaneMode(state.paneMode);
    updateViewChrome();
  });
  $('zoomIn').addEventListener('click', () => layout.setZoom(layout.getZoom() + 0.1));
  $('zoomOut').addEventListener('click', () => layout.setZoom(layout.getZoom() - 0.1));
  $('zoomFit').addEventListener('click', () => layout.resetZoom());

  // Ctrl + 滚轮缩放原版页面
  $('paper').addEventListener('wheel', (event) => {
    if (!event.ctrlKey || !useLayout()) return;
    event.preventDefault();
    layout.setZoom(layout.getZoom() + (event.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });

  // 随时记录当前标签的阅读位置；等切走再读会被平滑滚动动画打断。
  $('paper').addEventListener('scroll', () => {
    const tab = currentTab();
    if (tab) tab.scrollTop = $('paper').scrollTop;
  }, { passive: true });

  const dropzone = $('dropzone');
  dropzone.addEventListener('click', () => $('fileInput').click());
  dropzone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') $('fileInput').click();
  });

  let dragDepth = 0;
  ['dragenter', 'dragover'].forEach((type) => {
    document.addEventListener(type, (event) => {
      if (!event.dataTransfer?.types?.includes('Files')) return;
      event.preventDefault();
      if (type === 'dragenter') dragDepth += 1;
      dropzone.classList.add('dragging');
    });
  });
  document.addEventListener('dragleave', (event) => {
    if (!event.dataTransfer?.types?.includes('Files')) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropzone.classList.remove('dragging');
  });
  document.addEventListener('drop', (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    dragDepth = 0;
    dropzone.classList.remove('dragging');
    handleFile(event.dataTransfer.files[0]);
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('#moreMenu') && !event.target.closest('#btnMore')) closeMoreMenu();
    if (!event.target.closest('#docMenu') && !event.target.closest('#btnDocMore')) closeDocMenu();
    if (exportMenu && !exportMenu.contains(event.target) && !event.target.closest('#btnExport')) closeExportMenu();
  });
  document.addEventListener('mousemove', () => {
    if (!isFocusMode()) return;
    const now = Date.now();
    if (now - lastPointerMove < 140) return;
    lastPointerMove = now;
    scheduleFocusBarHide();
  });
  document.addEventListener('mousedown', (event) => {
    if (!isFocusMode() || !document.body.classList.contains('toc-open')) return;
    if (event.target.closest('#sidePanel, #focusBar, #focusEdge')) return;
    toggleFocusToc(false);
  });
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement && isFocusMode() && nativeFullscreen) {
      exitFocusMode({ fromFullscreen: true });
    }
  });

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'f') {
      closeMoreMenu();
      closeDocMenu();
    }
    // 标签页快捷键（Alt 组合不会和浏览器自带快捷键冲突）
    if (event.altKey && !event.ctrlKey && !event.metaKey && !isTypingTarget(event.target)) {
      const key = event.key.toLowerCase();
      if (key === 't') {
        event.preventDefault();
        createTab();
        return;
      }
      if (key === 'w') {
        event.preventDefault();
        if (currentTab()) closeTab(currentTab().id);
        return;
      }
      if (/^[1-9]$/.test(event.key)) {
        event.preventDefault();
        const index = event.key === '9' ? tabs.length - 1 : Number(event.key) - 1;
        if (tabs[index]) activateTab(tabs[index].id);
        return;
      }
    }
    if (event.key === 'Escape') {
      closeMoreMenu();
      closeDocMenu();
      closeExportMenu();
      search.closeSearch();
      closeModal($('settingsModal'));
      closeModal($('aboutModal'));
      $('historyDrawer').hidden = true;
      if (isFocusMode()) {
        if (document.body.classList.contains('toc-open')) {
          toggleFocusToc(false);
          return;
        }
        exitFocusMode();
      }
    }
  });
  window.addEventListener('resize', () => {
    if (useLayout()) layout.setZoom(layout.getZoom());
  });
}

async function init() {
  bind();
  search.initSearch({
    getDocument: () => state.doc,
    onUnavailable: () => toast({
      title: '先打开一篇文档',
      message: '打开文档后按 Ctrl+F 查找原文或译文。',
      type: 'info',
      duration: 3000,
    }),
  });
  initSettings({ onSettingsChanged: () => refreshSettings().catch(() => {}) });
  initHistory({ onOpen: (docId) => openDocumentById(docId) });
  createTab();
  try {
    await refreshSettings();
  } catch (err) {
    toastError(err);
  }
}

init();