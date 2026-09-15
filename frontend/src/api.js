const BASE = '/api';
const TOKEN_KEY = 'atlas_token';

/** 令牌只在浏览器本地保存,服务端不存会话;请求统一从这里带 Authorization。 */
export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY) || '',
  set: (token) => {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  },
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function request(path, options = {}) {
  const token = tokenStore.get();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${BASE}${path}`, { ...options, headers });

  if (response.status === 401 && !path.startsWith('/auth/')) {
    // 令牌过期/失效:清掉本地状态,上层会提示重新登录
    tokenStore.clear();
    throw new Error('登录状态已失效,请重新登录');
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_) {
      /* 忽略非 JSON 响应 */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request('/health'),

  // 账号
  register: (username, password) => request('/auth/register', json('POST', { username, password })),
  login: (username, password) => request('/auth/login', json('POST', { username, password })),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),

  // 文档
  listDocuments: () => request('/documents'),
  uploadDocument: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/documents/upload', { method: 'POST', body: form });
  },
  listChunks: (id) => request(`/documents/${id}/chunks`),
  importSnapshot: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/documents/import', { method: 'POST', body: form });
  },
  /** 抽取 / 重抽;fast=true 只处理前若干块,用覆盖面换时间 */
  extractDocument: (id, fast = false) =>
    request(`/documents/${id}/extract?fast=${fast ? 'true' : 'false'}`, { method: 'POST' }),
  /** 一次拿回所有正在抽取的文档的进度(纯内存,不查库) */
  documentProgress: () => request('/documents/progress'),
  deleteDocument: (id) => request(`/documents/${id}`, { method: 'DELETE' }),

  // 图(spaceId 非空时只取该知识空间内的内容)
  getGraph: (spaceId = null) => request(spaceId ? `/graph?space_id=${spaceId}` : '/graph'),
  graphStats: (spaceId = null) =>
    request(spaceId ? `/graph/stats?space_id=${spaceId}` : '/graph/stats'),
  search: (q) => request(`/graph/search?q=${encodeURIComponent(q)}`),
  reviewQueue: () => request('/graph/review'),
  findPath: (sourceId, targetId) =>
    request(`/graph/path?source_id=${sourceId}&target_id=${targetId}`),

  entityDetail: (id) => request(`/graph/entities/${id}`),
  createEntity: (body) => request('/graph/entities', json('POST', body)),
  updateEntity: (id, body) => request(`/graph/entities/${id}`, json('PATCH', body)),
  deleteEntity: (id) => request(`/graph/entities/${id}`, { method: 'DELETE' }),

  createRelation: (body) => request('/graph/relations', json('POST', body)),
  updateRelation: (id, body) => request(`/graph/relations/${id}`, json('PATCH', body)),
  deleteRelation: (id) => request(`/graph/relations/${id}`, { method: 'DELETE' }),

  // 实体消歧(重复实体合并)
  listDuplicates: () => request('/graph/duplicates'),
  mergeEntities: (keepId, mergeIds) =>
    request('/graph/merge', json('POST', { keep_id: keepId, merge_ids: mergeIds })),

  // 撤销 / 重做栈
  listTrash: () => request('/graph/trash'),
  undoLast: () => request('/graph/undo', { method: 'POST' }),
  undoSnapshot: (snapshotId) => request(`/graph/undo/${snapshotId}`, { method: 'POST' }),
  undoMany: (ids) => request('/graph/trash/undo-batch', json('POST', { ids })),
  redoLast: () => request('/graph/redo', { method: 'POST' }),
  clearTrash: () => request('/graph/trash', { method: 'DELETE' }),

  // 问答
  ask: (question, maxHops = 2, spaceId = null) =>
    request('/qa/ask', json('POST', { question, max_hops: maxHops, space_id: spaceId })),
  listRecords: () => request('/qa/records'),
  getRecord: (id) => request(`/qa/records/${id}`),

  // 个人主页与关注(星标)
  dashboard: () => request('/me/dashboard'),
  listStarred: () => request('/graph/starred'),
  toggleStar: (id, starred) =>
    request(`/graph/entities/${id}`, json('PATCH', { starred })),
  getInsight: () => request('/me/insight'),

  // 个性化设置(问答偏好)
  getSettings: () => request('/me/settings'),
  updateSettings: (body) => request('/me/settings', json('PUT', body)),

  // 知识空间
  listSpaces: () => request('/spaces'),
  createSpace: (body) => request('/spaces', json('POST', body)),
  updateSpace: (id, body) => request(`/spaces/${id}`, json('PATCH', body)),
  deleteSpace: (id) => request(`/spaces/${id}`, { method: 'DELETE' }),
  setDocumentSpace: (id, spaceId) =>
    request(`/documents/${id}/space`, json('PATCH', { space_id: spaceId })),

  // 知识缺口
  getGaps: (spaceId = null) =>
    request(spaceId ? `/me/gaps?space_id=${spaceId}` : '/me/gaps'),

  // 只读分享
  getShared: (token) => request(`/shared/${token}`),
  shareSpace: (id) => request(`/spaces/${id}/share`, { method: 'POST' }),
  revokeShare: (id) => request(`/spaces/${id}/share`, { method: 'DELETE' }),

  // 开发:演示数据(无需密钥)
  seedMock: () => request('/dev/seed', { method: 'POST' }),
  resetMock: () => request('/dev/reset', { method: 'POST' }),
};
