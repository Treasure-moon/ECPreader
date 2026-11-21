import { api } from './api.js';
import { toast, toastError, escapeHtml, confirmAction, download, openModal, closeModal } from './ui.js';

const modal = document.getElementById('settingsModal');
let meta = null;
let onChanged = () => {};

const $ = (id) => document.getElementById(id);

export function initSettings({ onSettingsChanged }) {
  onChanged = onSettingsChanged || (() => {});
  modal.querySelectorAll('[data-close="settings"]').forEach((node) => {
    node.addEventListener('click', () => closeModal(modal));
  });
  modal.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      modal.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
      modal.querySelectorAll('.tabpane').forEach((pane) => {
        pane.classList.toggle('active', pane.dataset.pane === tab.dataset.tab);
      });
    });
  });
  modal.querySelectorAll('input[name="engineMode"]').forEach((radio) => {
    radio.addEventListener('change', toggleMode);
  });
  $('freeEmail').addEventListener('input', () => blockAuthorEmail($('freeEmail')));
  $('freeEmail').addEventListener('change', () => blockAuthorEmail($('freeEmail')));
  $('freeEmail').addEventListener('blur', () => blockAuthorEmail($('freeEmail')));
  $('aiProvider').addEventListener('change', () => {
    const preset = meta?.presets?.[$('aiProvider').value];
    if (preset) {
      $('aiBaseUrl').value = preset.base_url || '';
      $('aiModel').value = preset.model || '';
    }
    updateKeyState();
    updatePresetNote();
  });
  $('freeProvider').addEventListener('change', updateFreeNote);
  $('aiTemp').addEventListener('input', () => {
    $('aiTempOut').textContent = Number($('aiTemp').value).toFixed(2);
  });
  $('btnSaveSettings').addEventListener('click', () => saveSettings());
  $('btnSaveKey').addEventListener('click', saveKey);
  $('btnClearKey').addEventListener('click', clearKey);
  $('btnTest').addEventListener('click', testConnection);
  $('btnPickDataDir').addEventListener('click', pickDataDir);
  $('btnOpenDataDir').addEventListener('click', async () => {
    try {
      await api.post('/api/system/reveal', {});
    } catch (err) {
      toastError(err);
    }
  });
  $('btnBackup').addEventListener('click', () => download(api.downloadUrl('/api/history/export')));
  $('btnClearHistory').addEventListener('click', async () => {
    if (!confirmAction('确定清空全部翻译历史吗？', '文档与译文不会被删除，只清除历史记录。')) return;
    try {
      await api.del('/api/history');
      toast({ title: '历史记录已清空', type: 'success' });
      onChanged();
    } catch (err) {
      toastError(err);
    }
  });
  $('btnClearDocs').addEventListener('click', async () => {
    if (!confirmAction('确定删除全部文档与译文吗？', '该操作不可恢复，数据库中的文档与段落译文都会被删除。')) return;
    try {
      const result = await api.del('/api/documents');
      toast({ title: `已删除 ${result.deleted ?? 0} 篇文档`, type: 'success' });
      onChanged();
    } catch (err) {
      toastError(err);
    }
  });
}

function currentMode() {
  return modal.querySelector('input[name="engineMode"]:checked')?.value || 'free';
}

function toggleMode() {
  const mode = currentMode();
  $('aiFields').hidden = mode !== 'ai';
  $('freeFields').hidden = mode !== 'free';
}

function fillSelect(select, items) {
  select.innerHTML = '';
  for (const [key, info] of Object.entries(items || {})) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = info.label || key;
    select.appendChild(option);
  }
}

function fill(data) {
  meta = data;
  const settings = data.settings || {};
  modal.querySelectorAll('input[name="engineMode"]').forEach((radio) => {
    radio.checked = radio.value === settings.engine_mode;
  });
  toggleMode();

  fillSelect($('freeProvider'), data.free_providers);
  $('freeProvider').value = settings.free_provider || 'auto';
  $('freeEmail').value = settings.free_email || '';

  const presetMap = Object.fromEntries(Object.entries(data.presets || {}).map(([key, value]) => [key, value.label]));
  fillSelect($('aiProvider'), Object.fromEntries(Object.entries(presetMap).map(([key, label]) => [key, { label }])));
  $('aiProvider').value = settings.ai_provider || 'deepseek';
  $('aiBaseUrl').value = settings.ai_base_url || '';
  $('aiModel').value = settings.ai_model || '';
  $('aiConcurrency').value = String(settings.ai_concurrency || 3);
  $('aiTemp').value = String(settings.ai_temperature ?? 0.2);
  $('aiTempOut').textContent = Number(settings.ai_temperature ?? 0.2).toFixed(2);
  $('glossary').value = settings.glossary || '';
  $('trRefs').checked = Boolean(settings.translate_references);
  $('trFormulas').checked = Boolean(settings.translate_formulas);
  $('aiKey').value = '';
  $('testResult').textContent = '';
  $('testResult').className = 'test-result';

  updateKeyState();
  updateFreeNote();
  updatePresetNote();
  renderSecurity(data.security || {});
  renderAbout(data);
  $('dataDirPath').textContent = data.security?.data_dir || '';
  const fallback = data.security?.data_dir_fallback;
  $('dataDirHint').textContent = fallback
    ? `原来设置的数据目录不可用：${fallback}。当前已临时改用上方目录，请重新选择。`
    : '更改后会把现有数据一起迁移到新目录，重启软件生效。';
  $('dataDirHint').style.color = fallback ? '#B4342F' : '';
}

function updateKeyState() {
  const provider = $('aiProvider').value;
  const info = meta?.security?.masked_keys?.[provider];
  const node = $('keyState');
  if (info?.configured) {
    node.textContent = `已加密保存 ${info.masked}`;
    node.style.color = 'var(--accent)';
  } else {
    node.textContent = '未设置';
    node.style.color = 'var(--muted)';
  }
}

function updateFreeNote() {
  const note = meta?.free_providers?.[$('freeProvider').value]?.note || '';
  $('freeNote').textContent = note;
}

function updatePresetNote() {
  const preset = meta?.presets?.[$('aiProvider').value];
  $('presetNote').textContent = preset?.note ? `说明：${preset.note}` : '';
}

function renderSecurity(security) {
  const protection = security.protection === 'dpapi'
    ? 'Windows DPAPI（绑定当前账户）+ AES-256-GCM'
    : '文件权限保护（当前系统不支持 DPAPI）';
  $('secTitle').textContent = security.protection === 'dpapi' ? '凭据已加密保存在本机' : '凭据保存在本机（较弱保护）';
  $('secDesc').textContent =
    'API Key 仅以密文写入 vault.bin，主密钥由 Windows DPAPI 保护；其他 Windows 账户或换机后都无法解密。密钥永远不会返回给页面，也不会上传到任何服务器。';
  $('secKv').innerHTML = `
    <dt>加密方式</dt><dd>${escapeHtml(protection)}</dd>
    <dt>仓库文件</dt><dd>${escapeHtml(security.vault_file || '')}</dd>
    <dt>数据目录</dt><dd>${escapeHtml(security.data_dir || '')}</dd>
    <dt>数据库</dt><dd>${escapeHtml(security.db_file || '')}</dd>
  `;
}

function renderAbout(data) {
  const version = document.querySelector('meta[name="ecp-version"]')?.content || '';
  $('aboutBox').innerHTML = `ECPreader v${escapeHtml(version)} · 本地运行 · 无遥测<br>
    文档解析：PyMuPDF / python-docx　　翻译：MyMemory / Google / Bing 与 OpenAI 兼容接口`;
}

export async function openSettings() {
  try {
    const data = await api.get('/api/settings');
    fill(data);
    openModal(modal);
  } catch (err) {
    toastError(err);
  }
}

const AUTHOR_EMAIL = 'treasure_moon@163.com';

function blockAuthorEmail(field) {
  if (!field || field.value.trim().toLowerCase() !== AUTHOR_EMAIL) return false;
  field.value = '';
  window.alert('！！请不要填作者的邮箱！！');
  return true;
}

function collect() {
  return {
    engine_mode: currentMode(),
    free_provider: $('freeProvider').value,
    free_email: $('freeEmail').value.trim(),
    ai_provider: $('aiProvider').value,
    ai_base_url: $('aiBaseUrl').value.trim(),
    ai_model: $('aiModel').value.trim(),
    ai_concurrency: Number($('aiConcurrency').value) || 3,
    ai_temperature: Number($('aiTemp').value),
    glossary: $('glossary').value,
    translate_references: $('trRefs').checked,
    translate_formulas: $('trFormulas').checked,
  };
}

async function saveSettings({ silent = false } = {}) {
  try {
    if (blockAuthorEmail($('freeEmail'))) return false;
    const body = collect();
    if (!body.ai_base_url && body.engine_mode === 'ai') {
      toast({ title: '请填写接口地址', message: 'AI 引擎需要接口地址，例如 https://api.deepseek.com/v1。', type: 'warn' });
      return false;
    }
    const data = await api.put('/api/settings', { settings: body });
    meta = data;
    fill(data);
    onChanged(data);
    if (!silent) {
      toast({ title: '设置已保存', message: '翻译引擎将会在下次翻译时生效。', type: 'success', duration: 4000 });
    }
    return true;
  } catch (err) {
    toastError(err);
    return false;
  }
}

async function pickDataDir() {
  const current = meta?.security?.data_dir || '';
  try {
    const picked = await api.post('/api/system/pick-folder', { initial: current });
    if (!picked?.path) return;
    if (picked.path === current) {
      toast({ title: '新目录与当前目录相同', type: 'warn' });
      return;
    }
    if (!window.confirm(`将数据目录改为：
${picked.path}

继续吗？`)) return;
    const migrate = window.confirm('是否把现有数据复制到新目录？「确定」= 复制（推荐），「取消」= 新目录从空白开始');
    const result = await api.post('/api/system/data-dir', { path: picked.path, migrate });
    meta.security.data_dir = result.data_dir;
    $('dataDirPath').textContent = result.data_dir;
    $('dataDirHint').textContent = result.migrated
      ? '数据已复制到新目录，重启后生效。'
      : '已保存新目录，重启后生效（新目录为空）。';
    toast({
      title: '数据目录已更新',
      message: '重启软件后才会切换到新目录。',
      type: 'success',
      duration: 6000,
    });
    if (window.confirm('现在重启软件吗？')) {
      try {
        await api.post('/api/system/restart', {});
      } catch (err) {
        // 重启时连接会断开，这是预期行为
      }
    }
  } catch (err) {
    toastError(err);
  }
}

async function saveKey() {
  const provider = $('aiProvider').value;
  const key = $('aiKey').value.trim();
  if (!key) {
    toast({ title: '请先粘贴 API Key', type: 'warn' });
    return;
  }
  try {
    await api.post('/api/settings/credentials', { provider, api_key: key });
    $('aiKey').value = '';
    const data = await api.get('/api/settings');
    meta = data;
    updateKeyState();
    toast({ title: '密钥已加密保存', message: '只保存在本机 vault.bin 中，页面无法读取明文。', type: 'success' });
    onChanged();
  } catch (err) {
    toastError(err);
  }
}

async function clearKey() {
  const provider = $('aiProvider').value;
  if (!confirmAction('确定清除该服务商的 API Key 吗？', '清除后需要使用 AI 翻译时必须重新填写。')) return;
  try {
    await api.post('/api/settings/credentials', { provider, clear: true });
    const data = await api.get('/api/settings');
    meta = data;
    updateKeyState();
    toast({ title: '密钥已清除', type: 'success' });
    onChanged();
  } catch (err) {
    toastError(err);
  }
}

async function testConnection() {
  const result = $('testResult');
  result.textContent = '正在测试…';
  result.className = 'test-result';
  $('btnTest').disabled = true;
  try {
    const saved = await saveSettings({ silent: true });
    if (!saved) {
      result.textContent = '保存设置失败，无法测试';
      result.className = 'test-result err';
      return;
    }
    const data = await api.post('/api/settings/test', { mode: currentMode() });
    if (data.ok) {
      result.textContent = `连接成功 · ${data.engine} · ${data.latency_ms}ms`;
      result.className = 'test-result ok';
      toast({
        title: '连接成功',
        message: `${data.engine}（${data.latency_ms}ms）`,
        hint: data.sample ? `测试译文：${data.sample}` : '',
        type: 'success',
        duration: 6000,
      });
    } else {
      const error = data.error || {};
      result.textContent = `${error.message || '连接失败'}${error.hint ? ' · ' + error.hint : ''}`;
      result.className = 'test-result err';
      toast({ title: error.message || '连接失败', message: error.hint || '', type: 'error', duration: 12000 });
    }
  } catch (err) {
    toastError(err);
    result.textContent = '测试失败';
    result.className = 'test-result err';
  } finally {
    $('btnTest').disabled = false;
  }
}