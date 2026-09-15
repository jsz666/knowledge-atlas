/** 个性化偏好:主题与图谱视图设置存在浏览器本地,不上传服务器。 */

const PREFIX = 'atlas_pref_';

const DEFAULTS = {
  theme: 'dark',
  graphLayout: 'fcose',
  typeFilter: [],
  statusFilter: [],
  onlyStarred: false,
  edgeLabels: true,
  colorByType: true,
};

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (_) {
    return fallback; // 数据损坏时退回默认值,不影响主流程
  }
}

function write(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (_) {
    /* 隐私模式下 localStorage 可能不可写,忽略即可 */
  }
}

export const prefs = {
  get: (key) => read(key, DEFAULTS[key]),
  set: (key, value) => write(key, value),
  reset: () => Object.keys(DEFAULTS).forEach((key) => write(key, DEFAULTS[key])),
};

/** 把主题写到 <html data-theme>,CSS 变量据此切换明暗配色。 */
export function applyTheme(theme) {
  const value = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', value);
  return value;
}
