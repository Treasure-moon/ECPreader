const token = document.querySelector('meta[name="ecp-token"]')?.content || '';

export class ApiError extends Error {
  constructor(status, payload) {
    const detail = payload && payload.detail;
    const info = typeof detail === 'object' && detail ? detail : { message: typeof detail === 'string' ? detail : '请求失败' };
    super(info.message || '请求失败');
    this.status = status;
    this.payload = payload;
    this.info = info;
  }

  get code() { return this.info.code || (this.status === 401 ? 'unauthorized' : 'unknown'); }
  get hint() { return this.info.hint || ''; }
}

async function request(path, { method = 'GET', body, form = false } = {}) {
  const headers = { 'X-ECP-Token': token };
  let payload;
  if (body !== undefined) {
    if (form) {
      payload = body;
    } else {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
  }
  let response;
  try {
    response = await fetch(path, { method, headers, body: payload });
  } catch (err) {
    throw new ApiError(0, { detail: { code: 'network', message: '无法连接本地服务', hint: '请确认 ECPreader 的启动窗口仍在运行，然后刷新页面。' } });
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = { detail: text }; }
  }
  if (!response.ok) {
    throw new ApiError(response.status, data);
  }
  return data;
}

export const api = {
  token,
  get: (path) => request(path),
  post: (path, body = {}) => request(path, { method: 'POST', body }),
  put: (path, body = {}) => request(path, { method: 'PUT', body }),
  patch: (path, body = {}) => request(path, { method: 'PATCH', body }),
  del: (path) => request(path, { method: 'DELETE' }),
  upload: (path, formData) => request(path, { method: 'POST', body: formData, form: true }),
  downloadUrl: (path) => {
    const joiner = path.includes('?') ? '&' : '?';
    return `${path}${joiner}token=${encodeURIComponent(token)}`;
  },
};