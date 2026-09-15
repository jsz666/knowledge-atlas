import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api.js';
import { prefs } from '../prefs.js';

// fCoSE:内置 cose 的加速增强版,作为默认力导向布局
cytoscape.use(fcose);

// ELK 体积较大,改为按需懒加载:只有切到 ELK 布局时才引入并注册
let elkPromise = null;
const ensureElk = () => {
  if (!elkPromise) {
    elkPromise = import('cytoscape-elk').then((mod) => {
      cytoscape.use(mod.default || mod);
    });
  }
  return elkPromise;
};

const ENTITY_TYPES = [
  'concept', 'method', 'model', 'paper', 'person', 'tool', 'dataset', 'org',
];

const LAYOUTS = [
  { value: 'fcose', label: '力导向 (fCoSE)' },
  { value: 'cose', label: '力导向 (cose 经典)' },
  { value: 'elk-layered', label: '层次 (ELK layered)' },
  { value: 'circle', label: '环形 (circle)' },
  { value: 'concentric', label: '同心圆 (concentric)' },
  { value: 'grid', label: '网格 (grid)' },
  { value: 'breadthfirst', label: '层次 (breadthfirst)' },
];

/** 关系类型配色:让「使用 / 改进 / 对比」等语义一眼可辨,而不是所有连线一个颜色。 */
const RELATION_COLORS = {
  related_to: '#94a3b8',
  derives_from: '#38bdf8',
  improves_on: '#34d399',
  part_of: '#fbbf24',
  uses: '#a78bfa',
  compared_with: '#fb7185',
  proposed_by: '#22d3ee',
};

/**
 * 布局参数:力导向(cose)下加大斥力、边长与迭代次数,并为节点与组件留出间距,
 * 让整张图更舒展,能明显减少连线交叉与节点重叠。
 */
const buildLayout = (name) => {
  // fCoSE:参数语义与 cose 接近,但默认值更保守,这里按本项目的视觉密度重新调优
  if (name === 'fcose') {
    return {
      name,
      animate: false,
      randomize: true,
      padding: 40,
      quality: 'default', // draft(仅谱布局) / default / proof(最慢最好)
      nodeDimensionsIncludeLabels: true, // 标签尺寸参与布局,避免压字
      packComponents: false, // 不引入 layout-utilities,断连分量交给后续手动整理
      nodeSeparation: 120,
      idealEdgeLength: 130,
      nodeRepulsion: 12000,
      edgeElasticity: 0.45,
      gravity: 0.25,
      numIter: 2000,
    };
  }
  // ELK layered:分层布局,对有向关系能显著减少边交叉
  if (name === 'elk-layered') {
    return {
      name: 'elk',
      animate: false,
      padding: 40,
      nodeDimensionsIncludeLabels: true,
      elk: {
        algorithm: 'layered',
        'elk.direction': 'DOWN',
        'elk.spacing.nodeNode': '50',
        'elk.layered.spacing.nodeNodeBetweenLayers': '80',
        'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
        'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
      },
    };
  }
  if (name === 'cose') {
    return {
      name,
      animate: false,
      randomize: true,
      padding: 40,
      nodeOverlap: 24, // 节点之间留白,避免挤成一团
      componentSpacing: 130, // 不相连的子图彼此拉开
      nodeDimensionsIncludeLabels: true, // 布局时把文字标签尺寸也算进去
      idealEdgeLength: 120, // 连线更长 → 走向更清楚
      nodeRepulsion: 24000, // 斥力更大 → 节点更分散
      edgeElasticity: 80,
      gravity: 0.28,
      numIter: 1200, // 迭代更充分 → 收敛到交叉更少的排布
      initialTemp: 220,
      coolingFactor: 0.95,
      minTemp: 1,
    };
  }
  return { name, animate: false, padding: 40 };
};

const STATUSES = ['confirmed', 'pending', 'rejected'];

// 超过这个节点数就进入"大图模式":只渲染核心节点,双击展开邻居
const LARGE_GRAPH_THRESHOLD = 80;
const LARGE_GRAPH_INITIAL = 40;

/** 图谱配色随主题变化:颜色集中在这里,切换明暗时整体重建 style。 */
const buildStyle = (theme, { colorByType = true, showEdgeLabels = true } = {}) => {
  const light = theme === 'light';
  const palette = {
    node: light ? '#2f6fe4' : '#4f8ef7',
    label: light ? '#1b2437' : '#e6edf8',
    border: light ? '#ffffff' : '#0b1220',
    pending: light ? '#b06f06' : '#f0a63c',
    confirmed: light ? '#14916a' : '#3fd18b',
    crossDoc: light ? '#8b5cf6' : '#c084fc',
    star: light ? '#c97a00' : '#ffd166',
    edge: light ? '#9aa8c4' : '#3d4f74',
    edgeLabel: light ? '#5b6b8c' : '#93a6c9',
    edgeBg: light ? '#ffffff' : '#0e1526',
    highlight: light ? '#111827' : '#ffffff',
  };

  // 打开「按关系类型着色」时,为每种关系类型生成一条样式规则
  const edgeByType = colorByType
    ? Object.entries(RELATION_COLORS).map(([type, color]) => ({
        selector: `edge[relType = "${type}"]`,
        style: { 'line-color': color, 'target-arrow-color': color },
      }))
    : [];

  return [
    {
      selector: 'node',
      style: {
        'background-color': palette.node,
        label: 'data(label)',
        color: palette.label,
        'font-size': 10,
        'min-zoomed-font-size': 6,
        'text-valign': 'center',
        'text-halign': 'right',
        'text-margin-x': 5,
        'text-wrap': 'wrap',
        'text-max-width': 140,
        width: 'data(size)',
        height: 'data(size)',
        'border-width': 1,
        'border-color': palette.border,
      },
    },
    {
      selector: 'node[status = "pending"]',
      style: { 'background-color': palette.pending, 'border-style': 'dashed' },
    },
    { selector: 'node[status = "confirmed"]', style: { 'background-color': palette.confirmed } },
    // 跨文档实体:出现在 2 篇及以上文档,用紫色描边突出
    { selector: 'node[?crossDoc]', style: { 'border-width': 3, 'border-color': palette.crossDoc } },
    // 我的关注:金色粗描边,在一堆节点里一眼就能找到
    {
      selector: 'node[?starred]',
      style: { 'border-width': 4, 'border-color': palette.star, 'font-weight': 'bold' },
    },
    {
      selector: 'edge',
      style: {
        width: 1.2,
        'line-color': palette.edge,
        'target-arrow-color': palette.edge,
        'target-arrow-shape': 'triangle',
        'arrow-scale': 1,
        'curve-style': 'bezier',
        label: showEdgeLabels ? 'data(label)' : '',
        'font-size': 8,
        'min-zoomed-font-size': 7,
        color: palette.edgeLabel,
        'text-rotation': 'autorotate',
        'text-margin-y': -4,
        'text-background-color': palette.edgeBg,
        'text-background-opacity': 0.75,
        'text-background-padding': 3,
      },
    },
    ...edgeByType,
    { selector: '.faded', style: { opacity: 0.15 } },
    { selector: '.highlight', style: { 'border-width': 3, 'border-color': palette.highlight } },
  ];
};

export default function GraphView({
  graph,
  selectedId,
  onSelectNode,
  onRefresh,
  theme = 'dark',
  readOnly = false,
}) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const onSelectRef = useRef(onSelectNode);
  onSelectRef.current = onSelectNode;
  const graphRef = useRef(graph);
  graphRef.current = graph;

  // 新增实体表单
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [etype, setEtype] = useState('concept');
  const [desc, setDesc] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  // 最短路径查询
  const [pathOpen, setPathOpen] = useState(false);
  const [pathSource, setPathSource] = useState('');
  const [pathTarget, setPathTarget] = useState('');
  const [pathInfo, setPathInfo] = useState(null);
  const [pathBusy, setPathBusy] = useState(false);
  const [pathErr, setPathErr] = useState('');

  // 视图:设置浮层、筛选、布局、tooltip、大图可见集
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 视图偏好从本地恢复:布局、筛选、只看关注,刷新后保持原样
  const [typeFilter, setTypeFilter] = useState(() => prefs.get('typeFilter'));
  const [statusFilter, setStatusFilter] = useState(() => prefs.get('statusFilter'));
  const [layout, setLayout] = useState(() => prefs.get('graphLayout'));
  const [onlyStarred, setOnlyStarred] = useState(() => prefs.get('onlyStarred'));
  const [edgeLabels, setEdgeLabels] = useState(() => prefs.get('edgeLabels'));
  const [colorByType, setColorByType] = useState(() => prefs.get('colorByType'));
  const [tooltip, setTooltip] = useState(null);
  const [visibleIds, setVisibleIds] = useState(null);
  const [showAll, setShowAll] = useState(false);

  const isLarge = graph.nodes.length > LARGE_GRAPH_THRESHOLD;

  // 偏好一变就写入本地,下次打开自动恢复
  useEffect(() => {
    prefs.set('typeFilter', typeFilter);
    prefs.set('statusFilter', statusFilter);
    prefs.set('graphLayout', layout);
    prefs.set('onlyStarred', onlyStarred);
    prefs.set('edgeLabels', edgeLabels);
    prefs.set('colorByType', colorByType);
  }, [typeFilter, statusFilter, layout, onlyStarred, edgeLabels, colorByType]);

  // 大图模式:默认只显示度数最高的若干核心节点
  useEffect(() => {
    if (!isLarge) {
      setVisibleIds(null);
      return;
    }
    const top = [...graph.nodes]
      .sort((a, b) => (b.degree || 0) - (a.degree || 0))
      .slice(0, LARGE_GRAPH_INITIAL)
      .map((node) => node.id);
    setVisibleIds(new Set(top));
  }, [graph.nodes, isLarge]);

  // 应用筛选(类型 / 状态)与大图可见集
  const visibleNodes = useMemo(() => {
    let nodes = graph.nodes;
    if (typeFilter.length) {
      nodes = nodes.filter((node) => typeFilter.includes(node.type));
    }
    if (statusFilter.length) {
      nodes = nodes.filter((node) => statusFilter.includes(node.status));
    }
    if (onlyStarred) {
      nodes = nodes.filter((node) => node.starred);
    }
    if (isLarge && !showAll && visibleIds) {
      nodes = nodes.filter((node) => visibleIds.has(node.id));
    }
    return nodes;
  }, [graph.nodes, typeFilter, statusFilter, onlyStarred, isLarge, showAll, visibleIds]);

  const visibleEdges = useMemo(() => {
    const allowed = new Set(visibleNodes.map((node) => node.id));
    return graph.edges.filter(
      (edge) => allowed.has(edge.source) && allowed.has(edge.target)
    );
  }, [graph.edges, visibleNodes]);

  // 初始化一次:注册交互事件
  useEffect(() => {
    const cy = cytoscape({
      container: containerRef.current,
      style: buildStyle(theme, { colorByType, showEdgeLabels: edgeLabels }),
      elements: [],
      layout: { name: 'fcose' },
      minZoom: 0.2,
      maxZoom: 3,
    });

    cy.on('tap', 'node', (event) => {
      onSelectRef.current?.(Number(event.target.id()));
    });

    // 双击展开邻居(大图渐进探索)
    cy.on('dbltap', 'node', (event) => {
      const nodeId = Number(event.target.id());
      const current = graphRef.current;
      setVisibleIds((prev) => {
        const next = new Set(prev || []);
        next.add(nodeId);
        current.edges.forEach((edge) => {
          if (edge.source === nodeId) next.add(edge.target);
          if (edge.target === nodeId) next.add(edge.source);
        });
        return next;
      });
    });

    // 悬停 tooltip
    cy.on('mouseover', 'node', (event) => {
      const node = event.target;
      const position = node.renderedPosition();
      setTooltip({
        x: position.x,
        y: position.y,
        label: node.data('label'),
        type: node.data('type'),
        status: node.data('status'),
        degree: node.data('degree'),
        docCount: node.data('docCount'),
        starred: node.data('starred'),
      });
    });
    cy.on('mouseout', 'node', () => setTooltip(null));
    cy.on('grab', 'node', () => setTooltip(null));

    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  // 主题 / 视图开关变化:只换样式,不重排布局(避免视觉抖动)
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.style(buildStyle(theme, { colorByType, showEdgeLabels: edgeLabels }));
  }, [theme, colorByType, edgeLabels]);

  // 用"指纹"判断图数据是否真的变了:避免每次刷新都重建元素并重排(视觉抖动)
  const graphSignature = useMemo(() => {
    const nodesKey = visibleNodes
      .map((n) => `${n.id}:${n.name}:${n.status}:${n.degree || 0}:${n.doc_count || 0}:${n.starred ? 1 : 0}`)
      .join('|');
    const edgesKey = visibleEdges
      .map((e) => `${e.id}:${e.source}-${e.target}:${e.relation_type}`)
      .join('|');
    return `${nodesKey}#${edgesKey}`;
  }, [visibleNodes, visibleEdges]);

  const elementsRef = useRef({ nodes: [], edges: [] });
  elementsRef.current = { nodes: visibleNodes, edges: visibleEdges };

  // 数据 / 筛选 / 布局变化:重建元素并重排
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const { nodes, edges } = elementsRef.current;
    const elements = [
      ...nodes.map((node) => ({
        data: {
          id: String(node.id),
          label: node.name,
          type: node.type,
          status: node.status,
          degree: node.degree || 0,
          docCount: node.doc_count || 0,
          crossDoc: (node.doc_count || 0) >= 2,
          starred: !!node.starred,
          size: 18 + Math.min(node.degree || 0, 12) * 2.5,
        },
      })),
      ...edges.map((edge) => ({
        data: {
          id: `e${edge.id}`,
          source: String(edge.source),
          target: String(edge.target),
          label: edge.relation_type,
          relType: edge.relation_type,
        },
      })),
    ];
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    // 同一对节点间若有多条关系(含双向),把曲线错开,避免箭头/连线相互压叠
    const bundles = new Map();
    cy.edges().forEach((edge) => {
      const s = edge.source().id();
      const t = edge.target().id();
      const key = s < t ? `${s}|${t}` : `${t}|${s}`;
      if (!bundles.has(key)) bundles.set(key, []);
      bundles.get(key).push(edge);
    });
    bundles.forEach((list) => {
      if (list.length < 2) return;
      const spacing = 34;
      list.forEach((edge, index) => {
        const offset = (index - (list.length - 1) / 2) * spacing;
        const distance = edge.source().id() > edge.target().id() ? -offset : offset;
        edge.style({
          'curve-style': 'unbundled-bezier',
          'control-point-distances': [distance],
          'control-point-weights': [0.5],
        });
      });
    });
    if (!elements.length) return;
    let cancelled = false;
    const runLayout = async () => {
      // ELK 首次使用时才异步加载并注册
      if (layout === 'elk-layered') {
        await ensureElk();
      }
      // 期间组件已卸载或又触发了一次重建,放弃本次布局
      if (cancelled || !cyRef.current) return;
      const cyNow = cyRef.current;
      cyNow.layout(buildLayout(layout)).run();
      cyNow.fit(cyNow.elements(), 40);
    };
    runLayout();
    return () => {
      cancelled = true;
    };
  }, [graphSignature, layout]);

  // 高亮:路径优先,其次选中节点
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('highlight faded');

    const pathIds = pathInfo?.found ? pathInfo.path_ids || [] : [];
    if (pathIds.length) {
      const ids = pathIds.map(String);
      let highlighted = cy.collection();
      ids.forEach((id) => {
        highlighted = highlighted.union(cy.getElementById(id));
      });
      for (let i = 0; i < ids.length - 1; i += 1) {
        const a = ids[i];
        const b = ids[i + 1];
        const segment = cy.edges().filter((edge) => {
          const s = edge.source().id();
          const t = edge.target().id();
          return (s === a && t === b) || (s === b && t === a);
        });
        highlighted = highlighted.union(segment);
      }
      highlighted.addClass('highlight');
      cy.elements().difference(highlighted).addClass('faded');
      return;
    }

    if (!selectedId) return;
    const node = cy.getElementById(String(selectedId));
    if (node.empty()) return;
    node.addClass('highlight');
    node.neighborhood().addClass('highlight');
    node.predecessors().addClass('highlight');
    cy.elements().difference(node.closedNeighborhood()).addClass('faded');
  }, [selectedId, graphSignature, pathInfo]);

  const createEntity = async (event) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setErr('请输入实体名称');
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const created = await api.createEntity({ name: trimmed, type: etype, description: desc.trim() });
      setName('');
      setDesc('');
      setShowForm(false);
      onRefresh?.();
      onSelectNode?.(created.id);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const runPath = async (event) => {
    event.preventDefault();
    if (!pathSource || !pathTarget) {
      setPathErr('请选择起点和终点');
      return;
    }
    if (pathSource === pathTarget) {
      setPathErr('起点和终点不能是同一个实体');
      return;
    }
    setPathBusy(true);
    setPathErr('');
    try {
      setPathInfo(await api.findPath(Number(pathSource), Number(pathTarget)));
    } catch (e) {
      setPathErr(e.message);
      setPathInfo(null);
    } finally {
      setPathBusy(false);
    }
  };

  const clearPath = () => {
    setPathInfo(null);
    setPathSource('');
    setPathTarget('');
    setPathErr('');
  };

  const zoomBy = (factor) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom(cy.zoom() * factor);
  };

  const fitView = () => {
    const cy = cyRef.current;
    if (cy && cy.elements().length) cy.fit(cy.elements(), 40);
  };

  const toggleInFilter = (value, list, setter) => {
    setter(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
  };

  const resetFilters = () => {
    setTypeFilter([]);
    setStatusFilter([]);
    setOnlyStarred(false);
  };

  const availableTypes = useMemo(
    () => [...new Set(graph.nodes.map((node) => node.type))].sort(),
    [graph.nodes]
  );

  return (
    <div className="graph-wrapper">
      <div className="graph-toolbar" style={readOnly ? { display: 'none' } : undefined}>
        <div className="graph-toolbar-row">
          <button className="ghost" onClick={() => setShowForm((value) => !value)}>+ 实体</button>
          <button className="ghost" onClick={() => setPathOpen((value) => !value)}>查路径</button>
          <button className="ghost" onClick={() => setSettingsOpen((value) => !value)}>视图</button>
        </div>

        {showForm && (
          <form className="entity-form" onSubmit={createEntity}>
            <input
              placeholder="实体名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoFocus
            />
            <select value={etype} onChange={(event) => setEtype(event.target.value)}>
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <input
              placeholder="描述(可选)"
              value={desc}
              onChange={(event) => setDesc(event.target.value)}
            />
            <div className="entity-form-actions">
              <button type="submit" disabled={busy}>创建</button>
              <button type="button" className="ghost" disabled={busy} onClick={() => setShowForm(false)}>
                取消
              </button>
            </div>
            {err && <span className="error">{err}</span>}
          </form>
        )}

        {pathOpen && (
          <form className="entity-form" onSubmit={runPath}>
            <select value={pathSource} onChange={(event) => setPathSource(event.target.value)}>
              <option value="">选择起点…</option>
              {graph.nodes.map((node) => (
                <option key={node.id} value={node.id}>{node.name}</option>
              ))}
            </select>
            <select value={pathTarget} onChange={(event) => setPathTarget(event.target.value)}>
              <option value="">选择终点…</option>
              {graph.nodes.map((node) => (
                <option key={node.id} value={node.id}>{node.name}</option>
              ))}
            </select>
            <div className="entity-form-actions">
              <button type="submit" disabled={pathBusy}>查询</button>
              <button type="button" className="ghost" onClick={clearPath}>清除</button>
            </div>
            {pathInfo && (
              <span className="path-result">
                {pathInfo.found
                  ? `${pathInfo.path.join(' → ')}（${pathInfo.path.length - 1} 跳）`
                  : '两点之间没有连通路径'}
              </span>
            )}
            {pathErr && <span className="error">{pathErr}</span>}
          </form>
        )}

        {settingsOpen && (
          <div className="graph-settings">
            <div className="settings-block">
              <h5>布局</h5>
              <select value={layout} onChange={(event) => setLayout(event.target.value)}>
                {LAYOUTS.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </div>

            <div className="settings-block">
              <h5>关注</h5>
              <label className="settings-switch">
                <input
                  type="checkbox"
                  checked={onlyStarred}
                  onChange={(event) => setOnlyStarred(event.target.checked)}
                />
                只看我关注的实体
              </label>
            </div>

            <div className="settings-block">
              <h5>连线显示</h5>
              <label className="settings-switch">
                <input
                  type="checkbox"
                  checked={edgeLabels}
                  onChange={(event) => setEdgeLabels(event.target.checked)}
                />
                显示关系标签
              </label>
              <label className="settings-switch">
                <input
                  type="checkbox"
                  checked={colorByType}
                  onChange={(event) => setColorByType(event.target.checked)}
                />
                按关系类型着色
              </label>
            </div>

            <div className="settings-block">
              <h5>按类型筛选</h5>
              <div className="filter-row">
                {availableTypes.map((type) => (
                  <label key={type}>
                    <input
                      type="checkbox"
                      checked={typeFilter.includes(type)}
                      onChange={() => toggleInFilter(type, typeFilter, setTypeFilter)}
                    />
                    {type}
                  </label>
                ))}
                {!availableTypes.length && <span className="hint">暂无实体</span>}
              </div>
            </div>

            <div className="settings-block">
              <h5>按状态筛选</h5>
              <div className="filter-row">
                {STATUSES.map((status) => (
                  <label key={status}>
                    <input
                      type="checkbox"
                      checked={statusFilter.includes(status)}
                      onChange={() => toggleInFilter(status, statusFilter, setStatusFilter)}
                    />
                    {status}
                  </label>
                ))}
              </div>
            </div>

            <div className="settings-block">
              <div className="entity-form-actions">
                <button className="ghost" onClick={resetFilters}>重置筛选</button>
                {isLarge && (
                  <button className="ghost" onClick={() => setShowAll((value) => !value)}>
                    {showAll ? `核心模式(${LARGE_GRAPH_INITIAL})` : `显示全部(${graph.nodes.length})`}
                  </button>
                )}
              </div>
              <span className="hint">
                当前显示 {visibleNodes.length} / {graph.nodes.length} 个节点
                {isLarge && !showAll ? ' · 双击节点展开邻居' : ''}
              </span>
            </div>
          </div>
        )}
      </div>

      <div ref={containerRef} className="graph-canvas" />

      <div className="graph-zoom">
        <button onClick={() => zoomBy(1.25)} title="放大">＋</button>
        <button onClick={() => zoomBy(0.8)} title="缩小">－</button>
        <button onClick={fitView} title="适应窗口">⤢</button>
      </div>

      {tooltip && (
        <div
          className="graph-tooltip"
          style={{ left: tooltip.x + 14, top: tooltip.y - 10 }}
        >
          <strong>{tooltip.label}</strong>
          <span>{tooltip.type} · {tooltip.status}</span>
          <span>关系 {tooltip.degree} · 跨 {tooltip.docCount} 篇文档</span>
          {tooltip.starred && <span>已关注</span>}
        </div>
      )}

      {!graph.nodes.length && (
        <div className="graph-empty">先点「载入示例」或「+ 实体」,星图会在这里长出来</div>
      )}
    </div>
  );
}
