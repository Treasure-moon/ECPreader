import { api } from './api.js';
import * as reader from './reader.js';
import * as layout from './layout.js';
import * as dict from './dict.js';
import { initSettings, openSettings } from './settings.js';
import { initHistory, openHistory } from './history.js';
import {
  toast,
  toastError,
  escapeHtml,
  showLoading,
  hideLoading,
  applyTheme,
  closeModal,
  download,
  formatDate,
} from './ui.js';

const state = {
  doc: null,
  settings: null,
  engine: null,
  jobId: null,
  cursor: 0,
  pollTimer: null,
  fontSize: 16,
  viewMode: localStorage.getItem('ecp.viewMode') === 'flow' ? 'flow' : 'layout',
  paneMode: 'dual',
  activeSeq: null,
  reparsed: new Set(),
};

let activeView = reader;

const $ = (id) => document.getElementById(id);
const MODE_LABEL = { free: '传统机翻', ai: 'AI 翻译' };

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
  const fontCtl = document.querySelector('.font-ctl');
  if (fontCtl) fontCtl.hidden = layoutOn;
  $('viewHint').textContent = layoutOn
    ? '选中英文或中文即同步高亮 · 双击单词查释义 · 顶部可重译/复制'
    : '划选句子，另一栏同步高亮 · 顶部可重译/复制当前段';
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

function renderCurrent({ preserveScroll = false } = {}) {
  if (!state.doc) return;
  const paper = $('paper');
  const ratio = preserveScroll && paper.scrollHeight
    ? paper.scrollTop / paper.scrollHeight
    : 0;
  const layoutOn = useLayout();
  updateViewChrome();
  activeView = layoutOn ? layout : reader;
  if (layoutOn) {
    layout.setPaneMode(state.paneMode);
    layout.renderDocument(state.doc, callbacks());
    reader.renderToc(state.doc.toc || [], (seq) => layout.scrollToSeq(seq));
    reader.renderMeta(state.doc);
  } else {
    reader.renderDocument(state.doc, callbacks());
  }
  reader.renderMeta(state.doc);
  state.activeSeq = null;
  updateBlockTools();
  if (preserveScroll) {
    requestAnimationFrame(() => {
      paper.scrollTop = ratio * paper.scrollHeight;
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
  state.activeSeq = null;
  updateBlockTools();
  $('readerView').hidden = true;
  $('homeView').hidden = false;
  $('docTitle').textContent = '';
  document.body.classList.remove('reading');
  dict.hideWordPopover();
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
  if (supportsLayout(document)) return document;
  if (state.reparsed.has(document.id)) return document;
  state.reparsed.add(document.id);
  showLoading('正在准备原版视图…');
  try {
    const data = await api.post(`/api/documents/${document.id}/reparse`, {});
    toast({
      title: '已启用原版视图',
      message: '已按论文原本的版面重新排布，图片、公式保持原样，原有译文已尽量保留。',
      type: 'success',
      duration: 5000,
    });
    return data.document;
  } catch (err) {
    toast({
      title: '暂时无法启用原版视图',
      message: err.info?.message || '已切换为逐句对照模式。',
      type: 'warn',
      duration: 6000,
    });
    return document;
  } finally {
    hideLoading();
  }
}

async function openDocument(document) {
  document = await ensureLayoutReady(document);
  state.doc = document;
  state.jobId = null;
  state.cursor = 0;
  showReader();
  $('docTitle').textContent = document.title || document.filename || '';
  state.activeSeq = null;
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
      message: '可以点击右上角「翻译全文」继续，或点击段落上的提示逐段翻译。',
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
    message: '可以单独翻译这一段，或点击右上角翻译全文。',
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
function updateProgress(job) {
  const wrap = $('progressWrap');
  wrap.hidden = false;
  const total = job.total || 1;
  const percent = Math.round(((job.done + job.failed) / total) * 100);
  $('progressBar').style.width = `${percent}%`;
  const failed = job.failed ? ` · 失败 ${job.failed}` : '';
  $('progressText').textContent = `正在翻译 ${job.done + job.failed}/${job.total} 段${failed} · ${MODE_LABEL[job.mode] || job.mode}`;
}

function hideProgress() {
  $('progressWrap').hidden = true;
  $('progressBar').style.width = '0%';
}

function applyTranslation(seq, translation) {
  const block = (state.doc?.blocks || []).find((item) => item.seq === seq);
  if (block) block.translation = translation;
  activeView.updateBlockTranslation(seq, translation);
}

async function startTranslation(force) {
  if (!state.doc || state.pollTimer) return;
  const mode = $('engineSelect').value || 'free';
  try {
    const data = await api.post(`/api/documents/${state.doc.id}/translate`, { force, mode });
    const job = data.job;
    state.jobId = job.id;
    state.cursor = 0;
    activeView.setTranslating(true);
    updateProgress(job);
    pollJob();
  } catch (err) {
    const actions = [];
    if (err.code === 'config_missing' || err.code === 'unauthorized') {
      actions.push({ label: '打开设置', primary: true, onClick: () => openSettings() });
      actions.push({ label: '改用免费机翻', onClick: () => switchMode('free') });
    }
    toastError(err, { actions });
  }
}

async function pollJob() {
  const jobId = state.jobId;
  if (!jobId) return;
  try {
    const data = await api.get(`/api/jobs/${jobId}?cursor=${state.cursor}`);
    const job = data.job;
    for (const update of job.updates || []) {
      applyTranslation(update.seq, update.translation);
    }
    state.cursor = job.cursor;
    updateProgress(job);
    if (job.status === 'running') {
      state.pollTimer = setTimeout(pollJob, 700);
    } else {
      finishJob(job);
    }
  } catch (err) {
    finishJob(null, err);
  }
}

function finishJob(job, error) {
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
  state.jobId = null;
  hideProgress();
  activeView.setTranslating(false);
  if (error) {
    toastError(error);
    return;
  }
  if (!job) return;
  if (job.status === 'done') {
    setDocStatus('translated');
    toast({
      title: '全文翻译完成',
      message: `共 ${job.total} 段，使用 ${MODE_LABEL[job.mode] || job.mode}。`,
      type: 'success',
      duration: 5000,
    });
  } else if (job.status === 'partial') {
    setDocStatus('partial');
    toast({
      title: `部分段落翻译失败（${job.failed} 段）`,
      message: job.error?.message || '可以点击失败段落的「重译」，或在设置中更换引擎。',
      hint: job.error?.hint || '',
      type: 'warn',
      duration: 12000,
      actions: [{ label: '打开设置', onClick: () => openSettings() }],
    });
  } else {
    setDocStatus('partial');
    const info = job.error || {};
    toast({
      title: info.message || '翻译失败',
      message: info.hint || '请检查翻译引擎设置后重试。',
      type: 'error',
      duration: 15000,
      actions: [
        { label: '打开设置', primary: true, onClick: () => openSettings() },
        { label: '改用免费机翻', onClick: () => switchMode('free') },
      ],
    });
  }
  renderRecent();
}

function setDocStatus(status) {
  if (state.doc) state.doc.status = status;
  reader.renderMeta(state.doc || {});
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
  if (!state.doc) return;
  const docId = state.doc.id;
  try {
    const data = await api.post(`/api/documents/${docId}/blocks/${seq}/translate`, {
      mode: $('engineSelect').value,
    });
    applyTranslation(seq, data.translation);
  } catch (err) {
    applyTranslation(seq, {
      status: 'error',
      segments: {},
      error: { message: err.info?.message || '重译失败', hint: err.info?.hint || '' },
    });
    toastError(err);
  }
}

async function cancelJob() {
  if (!state.jobId) return;
  try {
    await api.post(`/api/jobs/${state.jobId}/cancel`, {});
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
            <div class="r-title">${escapeHtml(doc.title)}</div>
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
let menuNode = null;

function closeMenu() {
  menuNode?.remove();
  menuNode = null;
}

function openExportMenu(anchor) {
  closeMenu();
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
  menu.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - menu.offsetHeight - 12)}px`;
  menu.style.left = `${Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12)}px`;
  menu.addEventListener('click', (event) => {
    const button = event.target.closest('[data-format]');
    if (!button) return;
    const format = button.dataset.format;
    download(api.downloadUrl(`/api/documents/${state.doc.id}/export?format=${format}`));
    closeMenu();
  });
  menuNode = menu;
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
  const reader = $('readerView');
  const side = $('sidePanel');
  reader.classList.toggle('side-collapsed', collapsed);
  side.setAttribute('aria-hidden', String(collapsed));
  side.inert = collapsed;
  const top = $('btnToggleSideTop');
  top.setAttribute('aria-expanded', String(!collapsed));
  top.title = collapsed ? '展开目录侧栏' : '收起目录侧栏';
  top.classList.toggle('is-active', !collapsed);
}

function toggleSide() {
  setSideCollapsed(!$('readerView').classList.contains('side-collapsed'));
}

function bind() {
  $('btnOpen').addEventListener('click', () => $('fileInput').click());
  $('btnPick').addEventListener('click', (event) => {
    event.stopPropagation();
    $('fileInput').click();
  });
  $('fileInput').addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) handleFile(file);
  });
  $('btnSettings').addEventListener('click', openSettings);
  $('engineBadge').addEventListener('click', openSettings);
  $('btnHistory').addEventListener('click', openHistory);
  $('btnHistoryHome').addEventListener('click', openHistory);
  $('btnTheme').addEventListener('click', cycleTheme);
  $('btnTranslateAll').addEventListener('click', () => startTranslation(false));
  $('btnRetranslate').addEventListener('click', () => {
    if (window.confirm('重新翻译会覆盖当前译文，确定继续吗？')) startTranslation(true);
  });
  $('btnCancelJob').addEventListener('click', cancelJob);
  $('btnExport').addEventListener('click', (event) => openExportMenu(event.currentTarget));
  $('btnBlockRetranslate').addEventListener('click', () => {
    if (state.activeSeq !== null) retranslateBlock(state.activeSeq);
  });
  $('btnBlockCopy').addEventListener('click', copySelectionOrBlock);
  $('fontMinus').addEventListener('click', () => setFontSize(state.fontSize - 1));
  $('fontPlus').addEventListener('click', () => setFontSize(state.fontSize + 1));
  $('engineSelect').addEventListener('change', (event) => switchMode(event.target.value));
  $('brandHome').addEventListener('click', showHome);
  $('btnToggleSide').addEventListener('click', () => toggleSide());
  $('btnToggleSideTop').addEventListener('click', () => toggleSide());

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
    if (menuNode && !menuNode.contains(event.target) && !event.target.closest('#btnExport')) closeMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeMenu();
      closeModal($('settingsModal'));
      $('historyDrawer').hidden = true;
    }
  });
  window.addEventListener('resize', () => {
    if (useLayout()) layout.setZoom(layout.getZoom());
  });
}

async function init() {
  bind();
  initSettings({ onSettingsChanged: () => refreshSettings().catch(() => {}) });
  initHistory({ onOpen: (docId) => openDocumentById(docId) });
  try {
    await refreshSettings();
  } catch (err) {
    toastError(err);
  }
  renderRecent();
}

init();