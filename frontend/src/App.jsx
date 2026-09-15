import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, tokenStore } from './api.js';
import AuthPanel from './components/AuthPanel.jsx';
import Dashboard from './components/Dashboard.jsx';
import DocumentPanel from './components/DocumentPanel.jsx';
import EntityDetail from './components/EntityDetail.jsx';
import GraphView from './components/GraphView.jsx';
import QAPanel from './components/QAPanel.jsx';
import ReviewPanel from './components/ReviewPanel.jsx';
import SettingsPanel from './components/SettingsPanel.jsx';
import SpaceBar from './components/SpaceBar.jsx';
import { useConfirm } from './components/Confirm.jsx';
import { useToast } from './components/Toast.jsx';
import { applyTheme, prefs } from './prefs.js';

const kindLabel = (kind) => (kind === 'entity' ? '实体' : '关系');

export default function App() {
  const toast = useToast();
  const confirm = useConfirm();

  const [health, setHealth] = useState(null);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [documents, setDocuments] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState('detail');
  const [keyword, setKeyword] = useState('');
  const [searchResult, setSearchResult] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [error, setError] = useState('');
  const [trash, setTrash] = useState({ undo: [], redo: [] });
  const [stats, setStats] = useState(null);
  const [stackOpen, setStackOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  // 账号
  const [user, setUser] = useState(null);
  const [authMode, setAuthMode] = useState('login');
  const [authOpen, setAuthOpen] = useState(false);

  // 个性化:明暗主题 + 「我的主页」数据
  const [theme, setTheme] = useState(() => prefs.get('theme'));
  const [dashboard, setDashboard] = useState(null);

  // 知识空间:null 表示「全部」,选中后图谱 / 统计 / 问答都限定在该空间内
  const [spaces, setSpaces] = useState([]);
  const [currentSpace, setCurrentSpace] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 撤销栈多选与过滤
  const [selected, setSelected] = useState([]);
  const [stackFilter, setStackFilter] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const searchRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const [docs, nextGraph, stacks, nextStats] = await Promise.all([
        api.listDocuments(),
        api.getGraph(currentSpace),
        api.listTrash(),
        api.graphStats(currentSpace),
      ]);
      setDocuments(docs);
      setGraph(nextGraph);
      setTrash({ undo: stacks.undo || [], redo: stacks.redo || [] });
      setStats(nextStats);

      if (user) {
        // 登录后才有主页与空间;单个接口失败不影响主流程
        const [dash, nextSpaces] = await Promise.all([
          api.dashboard().catch(() => null),
          api.listSpaces().catch(() => []),
        ]);
        setDashboard(dash);
        setSpaces(nextSpaces || []);
      } else {
        setDashboard(null);
        setSpaces([]);
      }
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [user, currentSpace]);

  const selectSpace = (spaceId) => {
    setCurrentSpace(spaceId);
    setSelectedId(null);
    setSearchResult(null);
  };

  useEffect(() => {
    refresh();
    api.health().then(setHealth).catch(() => setHealth(null));
    // 本地存过令牌就直接取回用户信息,刷新页面不必重新登录
    if (tokenStore.get()) {
      api.me()
        .then(setUser)
        .catch(() => {
          setUser(null);
          tokenStore.clear();
        });
    }
  }, [refresh]);

  // 主题写入 <html data-theme>,并记住选择
  useEffect(() => {
    applyTheme(theme);
    prefs.set('theme', theme);
  }, [theme]);

  // 只读分享:?share=token 时不加载自己的数据,只展示对方分享的空间
  const shareToken = useMemo(
    () => new URLSearchParams(window.location.search).get('share'),
    []
  );
  const [shared, setShared] = useState(null);
  const [shareError, setShareError] = useState('');

  useEffect(() => {
    if (!shareToken) return undefined;
    let alive = true;
    api
      .getShared(shareToken)
      .then((data) => alive && setShared(data))
      .catch((err) => alive && setShareError(err.message));
    return () => {
      alive = false;
    };
  }, [shareToken]);

  const nameMap = useMemo(
    () => Object.fromEntries(graph.nodes.map((node) => [node.id, node.name])),
    [graph]
  );

  const undoStack = trash.undo;
  const redoStack = trash.redo;
  const isEmpty = !loading && !documents.length && !graph.nodes.length;

  const filteredUndo = useMemo(() => {
    const kw = stackFilter.trim();
    if (!kw) return undoStack;
    return undoStack.filter((item) => item.label.includes(kw));
  }, [undoStack, stackFilter]);

  useEffect(() => {
    setSelected((prev) => prev.filter((id) => undoStack.some((item) => item.id === id)));
  }, [undoStack]);

  const runSearch = useCallback(
    async (value, auto = false) => {
      try {
        const data = await api.search(value);
        setSearchResult(data);
        if (!auto) setTab('search');
      } catch (err) {
        toast.error(err.message);
      }
    },
    [toast]
  );

  // 输入即搜(防抖),回车则立即搜并切到结果页
  useEffect(() => {
    const value = keyword.trim();
    if (!value) {
      setSearchResult(null);
      return undefined;
    }
    const timer = setTimeout(() => runSearch(value, true), 300);
    return () => clearTimeout(timer);
  }, [keyword, runSearch]);

  const handleAuthed = (nextUser, token, info) => {
    tokenStore.set(token);
    setUser(nextUser);
    setAuthOpen(false);
    setTab('home');
    setCurrentSpace(null);
    refresh();
    if (info?.registered) {
      toast.success(
        info.claimed
          ? `注册成功,已接管 ${info.claimed} 条历史数据`
          : '注册成功,已自动登录'
      );
    } else {
      toast.success(`欢迎回来,${nextUser.username}`);
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (_) {
      /* 退出失败也要清掉本地状态 */
    }
    tokenStore.clear();
    setUser(null);
    setDashboard(null);
    setSpaces([]);
    setCurrentSpace(null);
    setTab('detail');
    setSelectedId(null);
    setSelected([]);
    await refresh();
    toast.info('已退出登录');
  };

  const loadMock = async () => {
    try {
      const result = await api.seedMock();
      if (result && result.seeded === false) {
        toast.error(result.reason || '未载入示例数据');
      } else {
        toast.success('示例数据已载入');
      }
      await refresh();
      api.health().then(setHealth).catch(() => setHealth(null));
    } catch (err) {
      toast.error(err.message);
    }
  };

  const clearData = async () => {
    const ok = await confirm({
      title: '清空全部数据',
      message: '将删除所有文档 / 实体 / 关系 / 问答记录,且不可恢复。建议先导出快照备份。',
      confirmText: '清空',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.resetMock();
      await api.clearTrash();
      setSearchResult(null);
      setSelectedId(null);
      setSelected([]);
      await refresh();
      toast.success('已清空全部数据');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const handleImport = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    try {
      const result = await api.importSnapshot(file);
      await refresh();
      const added = result?.imported || {};
      toast.success(
        `导入完成 · 新增文档 ${added.documents} / 片段 ${added.chunks} / 实体 ${added.entities} / 关系 ${added.relations} / 证据 ${added.evidence}`,
        4500
      );
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const undoLast = async () => {
    setBusy(true);
    try {
      const result = await api.undoLast();
      await refresh();
      if (result?.restored?.kind === 'entity') {
        setSelectedId(result.restored.id);
      }
      toast.success(`已撤销删除「${result?.restored?.label ?? ''}」`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const undoSnapshot = async (snapshotId) => {
    setBusy(true);
    try {
      const result = await api.undoSnapshot(snapshotId);
      await refresh();
      if (result?.restored?.kind === 'entity') {
        setSelectedId(result.restored.id);
      }
      toast.success(`已撤销删除「${result?.restored?.label ?? ''}」`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const undoSelected = async () => {
    if (!selected.length) return;
    const ok = await confirm({
      title: '批量撤销',
      message: `确定撤销选中的 ${selected.length} 条删除?`,
      confirmText: '撤销',
    });
    if (!ok) return;
    setBusy(true);
    try {
      const result = await api.undoMany(selected);
      await refresh();
      const firstEntity = result?.restored?.find((item) => item.kind === 'entity');
      if (firstEntity) setSelectedId(firstEntity.id);
      setSelected([]);
      toast.success(`已撤销 ${selected.length} 条删除`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const redoLast = async () => {
    setBusy(true);
    try {
      await api.redoLast();
      await refresh();
      toast.info('已重做上一次删除');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const clearTrash = async () => {
    const ok = await confirm({
      title: '清空撤销记录',
      message: '清空后这些删除将无法再撤销。',
      confirmText: '清空记录',
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.clearTrash();
      setSelected([]);
      await refresh();
      toast.success('撤销记录已清空');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleSelect = (id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const allFilteredSelected =
    filteredUndo.length > 0 && filteredUndo.every((item) => selected.includes(item.id));

  const toggleSelectAll = () => {
    if (allFilteredSelected) {
      const filteredIds = filteredUndo.map((item) => item.id);
      setSelected((prev) => prev.filter((id) => !filteredIds.includes(id)));
    } else {
      setSelected((prev) => [...new Set([...prev, ...filteredUndo.map((item) => item.id)])]);
    }
  };

  // 键盘快捷键:Ctrl/⌘+Z 撤销、Ctrl/⌘+Shift+Z 重做、/ 聚焦搜索、Esc 收起浮层
  useEffect(() => {
    const onKeyDown = (event) => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable);

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        if (!undoStack.length && !redoStack.length) return;
        event.preventDefault();
        if (event.shiftKey) {
          if (redoStack.length) redoLast();
        } else if (undoStack.length) {
          undoLast();
        }
        return;
      }

      if (event.key === '/' && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }

      if (event.key === 'Escape') {
        if (exportOpen) setExportOpen(false);
        else if (stackOpen) setStackOpen(false);
        else if (keyword) setKeyword('');
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [undoStack.length, redoStack.length, exportOpen, stackOpen, keyword, undoLast, redoLast]);

  // 分享视图:只读,不请求任何需要登录的接口
  if (shareToken) {
    return (
      <div className="app">
        <header className="header">
          <div className="brand">
            只读分享 <span className="sub">{shared ? shared.title : '加载中'}</span>
          </div>
          {shared?.description && <span className="hint">{shared.description}</span>}
          <div className="status">
            <span className="tag tag-extracted">
              由 {shared?.owner || '…'} 分享 · 只读
            </span>
          </div>
          <a className="ghost share-back" href="/">返回我的星图</a>
        </header>

        {shareError && (
          <div className="banner error">
            <span>{shareError}</span>
          </div>
        )}

        {!shared && !shareError && (
          <div className="empty-state">
            <p className="hint">正在加载分享内容…</p>
          </div>
        )}

        {shared && (
          <>
            <div className="stats-bar">
              <span>文档 <b>{shared.stats.documents}</b></span>
              <span>实体 <b>{shared.stats.entities}</b></span>
              <span>关系 <b>{shared.stats.relations}</b></span>
              <span>跨文档实体 <b>{shared.stats.cross_doc_entities}</b></span>
            </div>
            <div className="main">
              <aside className="sidebar">
                <section className="panel">
                  <h2>文档 ({shared.documents.length})</h2>
                  <ul className="doc-list">
                    {shared.documents.map((doc) => (
                      <li key={doc.id}>
                        <div className="doc-title" title={doc.title}>{doc.title}</div>
                        <div className="doc-meta">
                          <span className="tag">{doc.file_type}</span>
                        </div>
                      </li>
                    ))}
                    {!shared.documents.length && <li className="hint">这个空间还没有文档</li>}
                  </ul>
                </section>
              </aside>
              <main className="center">
                <GraphView
                  graph={shared.graph}
                  selectedId={null}
                  onSelectNode={() => {}}
                  onRefresh={() => {}}
                  theme={theme}
                  readOnly
                />
              </main>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          知识星图 <span className="sub">文档关系探索器</span>
        </div>
        <div className="search-bar">
          <input
            ref={searchRef}
            placeholder="按实体、关系或关键词搜索( / 聚焦)"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && runSearch(keyword.trim())}
          />
          <button onClick={() => runSearch(keyword.trim())}>搜索</button>
        </div>
        <div className="dev-tools">
          <button className="ghost" onClick={loadMock} title="灌入示例数据(无需密钥)">载入示例</button>
          <button className="ghost" onClick={clearData} title="清空全部数据">清空</button>
          <label className="ghost import-btn" title="导入导出的 JSON 快照">
            导入
            <input type="file" accept=".json" onChange={handleImport} />
          </label>
          <div className="export-wrap">
            <button className="ghost" onClick={() => setExportOpen((v) => !v)}>导出 ▾</button>
            {exportOpen && (
              <div className="export-menu" onMouseLeave={() => setExportOpen(false)}>
                <a href="/api/export/json" download onClick={() => setExportOpen(false)}>
                  完整快照 (JSON)
                </a>
                <a
                  href="/api/export/csv?kind=entities"
                  download
                  onClick={() => setExportOpen(false)}
                >
                  实体清单 (CSV)
                </a>
                <a
                  href="/api/export/csv?kind=relations"
                  download
                  onClick={() => setExportOpen(false)}
                >
                  关系清单 (CSV)
                </a>
                <a href="/api/export/markdown" download onClick={() => setExportOpen(false)}>
                  关系清单 (Markdown)
                </a>
              </div>
            )}
          </div>
        </div>
        <div className="history-tools">
          <button
            disabled={busy || undoStack.length === 0}
            onClick={undoLast}
            title={undoStack.length ? `撤销:${undoStack[0].label}（Ctrl/⌘ + Z）` : '当前没有可撤销的操作'}
          >
            ↶ 撤销
            {undoStack.length > 0 && <span className="badge">{undoStack.length}</span>}
          </button>
          <button
            disabled={busy || redoStack.length === 0}
            onClick={redoLast}
            title={redoStack.length ? `重做（Ctrl/⌘ + Shift + Z）` : '当前没有可重做的操作'}
          >
            ↷ 重做
            {redoStack.length > 0 && <span className="badge">{redoStack.length}</span>}
          </button>
        </div>
        <button
          className="theme-toggle"
          onClick={() => setTheme((value) => (value === 'light' ? 'dark' : 'light'))}
          title="切换明暗配色(自动记住你的选择)"
        >
          {theme === 'light' ? '切换暗色' : '切换亮色'}
        </button>
        {user && (
          <button
            className="theme-toggle"
            onClick={() => setSettingsOpen(true)}
            title="个性化设置"
          >
            设置
          </button>
        )}
        <div className="user-area">
          {user ? (
            <>
              <span className="tag tag-extracted" title="当前登录账号">{user.username}</span>
              <button className="ghost" onClick={handleLogout}>退出</button>
            </>
          ) : (
            <button onClick={() => { setAuthMode('login'); setAuthOpen(true); }}>
              登录 / 注册
            </button>
          )}
        </div>
        <div className="status">
          {health ? (
            health.model_available ? (
              <span className="tag tag-extracted">模型已接入 · {health.model}</span>
            ) : (
              <span className="tag tag-pending">离线兜底模式</span>
            )
          ) : (
            <span className="tag">后端未连接</span>
          )}
        </div>
      </header>

      {stats && (
        <div className="stats-bar">
          <span>文档 <b>{stats.documents}</b></span>
          <span>实体 <b>{stats.entities}</b></span>
          <span>关系 <b>{stats.relations}</b></span>
          <span>跨文档实体 <b>{stats.cross_doc_entities}</b></span>
          {!!stats.starred_entities && (
            <span>我关注 <b>{stats.starred_entities}</b></span>
          )}
          <span className="warn">
            待确认 实体 <b>{stats.pending_entities}</b> / 关系 <b>{stats.pending_relations}</b>
          </span>
        </div>
      )}

      {error && (
        <div className="banner error">
          <span>{error}</span>
          <button className="banner-close" aria-label="关闭" onClick={() => setError('')}>×</button>
        </div>
      )}

      {undoStack.length > 0 && (
        <div className="undo-banner">
          <span>
            可撤销 {undoStack.length} 步 · 最近删除「{undoStack[0].label}」
            （{kindLabel(undoStack[0].kind)}）
          </span>
          <div className="edge-actions">
            <button disabled={busy} onClick={undoLast} title="Ctrl/⌘ + Z">撤销</button>
            {redoStack.length > 0 && (
              <button disabled={busy} onClick={redoLast} title="Ctrl/⌘ + Shift + Z">
                重做 ({redoStack.length})
              </button>
            )}
            <button className="ghost" disabled={busy} onClick={() => setStackOpen((v) => !v)}>
              {stackOpen ? '收起' : '展开'}
            </button>
            <button className="ghost" disabled={busy} onClick={clearTrash}>清空记录</button>
          </div>
        </div>
      )}

      {stackOpen && (undoStack.length > 0 || redoStack.length > 0) && (
        <div className="undo-stack-panel">
          <div className="stack-toolbar">
            <label className="stack-check">
              <input
                type="checkbox"
                checked={allFilteredSelected}
                onChange={toggleSelectAll}
                disabled={!filteredUndo.length}
              />
              全选
            </label>
            <span className="hint">已选 {selected.length} 条</span>
            <input
              className="stack-filter"
              placeholder="过滤标签关键词"
              value={stackFilter}
              onChange={(event) => setStackFilter(event.target.value)}
            />
            <div className="edge-actions">
              <button disabled={busy || !selected.length} onClick={undoSelected}>
                批量撤销 ({selected.length})
              </button>
              <button className="ghost" disabled={!selected.length} onClick={() => setSelected([])}>
                取消选择
              </button>
            </div>
          </div>

          <h4>撤销栈({undoStack.length}) · 可勾选批量撤销,或点某条单独撤销</h4>
          <ul>
            {filteredUndo.map((item, index) => (
              <li key={item.id}>
                <span>
                  <input
                    type="checkbox"
                    checked={selected.includes(item.id)}
                    onChange={() => toggleSelect(item.id)}
                  />
                  {index + 1}. [{kindLabel(item.kind)}] {item.label}
                  <span className="hint"> {item.created_at}</span>
                </span>
                <div className="edge-actions">
                  <button disabled={busy} onClick={() => undoSnapshot(item.id)}>撤销此条</button>
                </div>
              </li>
            ))}
            {!filteredUndo.length && <li><span className="hint">没有匹配的记录</span></li>}
          </ul>

          <h4>重做栈({redoStack.length}) · 按顺序回放</h4>
          <ul>
            {redoStack.map((item, index) => (
              <li key={item.id}>
                <span>
                  {index + 1}. [{kindLabel(item.kind)}] {item.label}
                  <span className="hint"> {item.created_at}</span>
                </span>
              </li>
            ))}
            {!redoStack.length && <li><span className="hint">暂无(撤销后可重做)</span></li>}
          </ul>
        </div>
      )}

      {authOpen && !user && (
        <div className="auth-overlay" onClick={() => setAuthOpen(false)}>
          <div onClick={(event) => event.stopPropagation()}>
            <AuthPanel
              mode={authMode}
              onModeChange={setAuthMode}
              onAuthed={handleAuthed}
              onToast={(message) => toast.error(message)}
            />
          </div>
        </div>
      )}

      {settingsOpen && user && (
        <div className="auth-overlay" onClick={() => setSettingsOpen(false)}>
          <div onClick={(event) => event.stopPropagation()}>
            <SettingsPanel
              onClose={() => setSettingsOpen(false)}
              onToast={(message) => toast.error(message)}
            />
          </div>
        </div>
      )}

      {!user && !loading && (
        <div className="banner info">
          <span>未登录:只能浏览公共数据,登录后才能上传文档、确认实体、合并与撤销删除。</span>
          <button onClick={() => { setAuthMode('login'); setAuthOpen(true); }}>登录 / 注册</button>
        </div>
      )}

      {loading ? (
        <div className="main">
          <aside className="sidebar">
            <section className="panel">
              <h2>文档</h2>
              <div className="skeleton skeleton-line" />
              <div className="skeleton skeleton-line" />
              <div className="skeleton skeleton-line short" />
            </section>
          </aside>
          <main className="center">
            <div className="skeleton skeleton-graph" />
            <div className="skeleton skeleton-line" />
          </main>
        </div>
      ) : (
        <div className="main">
          <aside className="sidebar">
            {user && (
              <SpaceBar
                spaces={spaces}
                currentId={currentSpace}
                onSelect={selectSpace}
                onChange={refresh}
                onToast={(message, type = 'error') =>
                  type === 'success' ? toast.success(message) : toast.error(message)
                }
              />
            )}
            <DocumentPanel
              documents={documents}
              spaces={spaces}
              onChange={() => {
                refresh();
                setRefreshKey((value) => value + 1);
              }}
              onToast={(message) => toast.error(message)}
            />
          </aside>

          <main className="center">
            {isEmpty ? (
              <div className="empty-state">
                <h3>还没有任何知识</h3>
                <p className="hint">
                  {user
                    ? '把 PDF / Markdown / TXT 拖到左侧上传,或先载入示例数据看看效果。'
                    : '登录后即可上传文档或载入示例数据,拥有属于自己的知识星图。'}
                </p>
                <div className="empty-actions">
                  {user ? (
                    <button onClick={loadMock}>载入示例数据</button>
                  ) : (
                    <button onClick={() => { setAuthMode('register'); setAuthOpen(true); }}>
                      登录 / 注册
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <GraphView
                graph={graph}
                selectedId={selectedId}
                onSelectNode={setSelectedId}
                onRefresh={refresh}
                theme={theme}
              />
            )}
            <div className="center-bottom">
              <div className="tabs">
                {user && (
                  <button className={tab === 'home' ? 'active' : ''} onClick={() => setTab('home')}>
                    我的主页
                  </button>
                )}
                <button className={tab === 'detail' ? 'active' : ''} onClick={() => setTab('detail')}>
                  节点详情
                </button>
                <button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')}>
                  人工确认
                </button>
                <button className={tab === 'qa' ? 'active' : ''} onClick={() => setTab('qa')}>
                  跨文档问答
                </button>
                <button className={tab === 'search' ? 'active' : ''} onClick={() => setTab('search')}>
                  搜索结果
                </button>
              </div>
              <div className="tab-content">
                {tab === 'home' && user && (
                  <Dashboard
                    data={dashboard}
                    onSelectNode={setSelectedId}
                    onRefresh={refresh}
                    onGoReview={() => setTab('review')}
                    onToast={(message) => toast.error(message)}
                  />
                )}
                {tab === 'detail' && (
                  <EntityDetail
                    entityId={selectedId}
                    nodes={graph.nodes}
                    onSelectNode={setSelectedId}
                    onRefresh={refresh}
                  />
                )}
                {tab === 'review' && (
                  <ReviewPanel
                    refreshKey={refreshKey}
                    nameMap={nameMap}
                    onChange={refresh}
                  />
                )}
                {tab === 'qa' && (
                  <QAPanel
                    spaceId={currentSpace}
                    spaceName={spaces.find((space) => space.id === currentSpace)?.name || ''}
                  />
                )}
                {tab === 'search' && (
                  <div className="panel-body">
                    {!searchResult && <p className="hint">输入关键词开始搜索(支持合并前的旧名字)</p>}
                    {searchResult && (
                      <>
                        <h4>命中实体 ({searchResult.nodes.length})</h4>
                        <div className="chip-row">
                          {searchResult.nodes.map((node) => (
                            <button
                              className="chip chip-button"
                              key={node.id}
                              onClick={() => setSelectedId(node.id)}
                            >
                              {node.name}
                            </button>
                          ))}
                        </div>
                        <h4>命中原文 ({searchResult.evidence.length})</h4>
                        <ul className="evidence-list">
                          {searchResult.evidence.map((item) => (
                            <li key={item.id}>
                              <div className="evidence-source">
                                {item.document_title} · {item.locator}
                              </div>
                              <div className="evidence-quote">{item.quote}</div>
                            </li>
                          ))}
                        </ul>
                        {!searchResult.nodes.length && !searchResult.evidence.length && (
                          <p className="hint">没有命中任何内容</p>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      )}
    </div>
  );
}
