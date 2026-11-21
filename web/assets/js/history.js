import { api } from './api.js';
import { escapeHtml, formatDate, confirmAction, download, toast, toastError, debounce } from './ui.js';

const drawer = document.getElementById('historyDrawer');
const listEl = document.getElementById('historyList');
const searchEl = document.getElementById('historySearch');
let onOpenDocument = () => {};

const STATUS_LABEL = {
  done: '已完成',
  running: '进行中',
  failed: '失败',
  partial: '部分成功',
  cancelled: '已取消',
};

const MODE_LABEL = { free: '传统机翻', ai: 'AI 翻译' };

export function initHistory({ onOpen: cb }) {
  onOpenDocument = cb || (() => {});
  drawer.querySelectorAll('[data-close="history"]').forEach((node) => {
    node.addEventListener('click', closeHistory);
  });
  document.getElementById('btnHistorySearch').addEventListener('click', load);
  searchEl.addEventListener('input', debounce(load, 320));
  searchEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') load();
  });
  document.getElementById('btnDrawerBackup').addEventListener('click', () => download(api.downloadUrl('/api/history/export')));
  document.getElementById('btnDrawerClear').addEventListener('click', async () => {
    if (!confirmAction('确定清空全部翻译历史吗？', '文档与译文不会被删除。')) return;
    try {
      await api.del('/api/history');
      toast({ title: '历史记录已清空', type: 'success' });
      load();
    } catch (err) {
      toastError(err);
    }
  });
  listEl.addEventListener('click', async (event) => {
    const item = event.target.closest('.history-item');
    if (!item) return;
    const docId = item.dataset.docId;
    const deleteButton = event.target.closest('[data-act="delete"]');
    if (deleteButton) {
      event.stopPropagation();
      if (!docId) return;
      if (!confirmAction('删除这篇文档及其译文？', '删除后无法恢复。')) return;
      try {
        await api.del(`/api/documents/${docId}`);
        toast({ title: '文档已删除', type: 'success' });
        load();
      } catch (err) {
        toastError(err);
      }
      return;
    }
    if (docId) {
      closeHistory();
      onOpenDocument(docId);
    }
  });
}

export function openHistory() {
  drawer.hidden = false;
  load();
}

export function closeHistory() {
  drawer.hidden = true;
}

async function load() {
  listEl.innerHTML = '<div class="history-empty">正在加载…</div>';
  try {
    const query = encodeURIComponent(searchEl.value.trim());
    const data = await api.get(`/api/history?limit=200&query=${query}`);
    const rows = data.history || [];
    if (!rows.length) {
      listEl.innerHTML = '<div class="history-empty">暂无历史记录。翻译过的文档会出现在这里。</div>';
      return;
    }
    listEl.innerHTML = rows
      .map((row) => {
        const status = STATUS_LABEL[row.status] || row.status;
        const count = row.block_total ? `${row.block_done || 0}/${row.block_total}` : '';
        const chars = row.char_count ? `${row.char_count} 字符` : '';
        return `<div class="history-item" data-doc-id="${escapeHtml(row.doc_id || '')}">
          <div class="h-title"><span>${escapeHtml(row.title)}</span><span class="h-date">${escapeHtml(formatDate(row.created_at))}</span></div>
          <div class="h-meta">
            <span>${escapeHtml(MODE_LABEL[row.mode] || row.mode || '')}</span>
            <span>${escapeHtml(row.engine || '')}${row.model ? ' · ' + escapeHtml(row.model) : ''}</span>
            <span>${escapeHtml(status)}${count ? ' · ' + escapeHtml(count) : ''}</span>
            ${chars ? `<span>${escapeHtml(chars)}</span>` : ''}
          </div>
          ${row.error ? `<div class="h-meta" style="color:var(--danger);margin-top:4px">${escapeHtml(row.error)}</div>` : ''}
          <div class="h-actions">
            <button class="btn small ghost" data-act="delete">删除文档</button>
          </div>
        </div>`;
      })
      .join('');
  } catch (err) {
    listEl.innerHTML = `<div class="history-empty">加载失败：${escapeHtml(err.message || '未知错误')}</div>`;
  }
}