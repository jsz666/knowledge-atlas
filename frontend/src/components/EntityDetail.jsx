import { useEffect, useState } from 'react';
import { api } from '../api.js';

// 与后端 config.RELATION_TYPES 保持一致
const RELATION_TYPES = [
  'related_to', 'derives_from', 'improves_on',
  'part_of', 'uses', 'compared_with', 'proposed_by',
];

export default function EntityDetail({ entityId, nodes = [], onSelectNode, onRefresh }) {
  const [detail, setDetail] = useState(null);
  const [type, setType] = useState('');
  const [error, setError] = useState('');

  // 新增关系表单
  const [relTarget, setRelTarget] = useState('');
  const [relType, setRelType] = useState('related_to');
  const [relDesc, setRelDesc] = useState('');
  const [relBusy, setRelBusy] = useState(false);
  const [delBusy, setDelBusy] = useState(false);
  const [starBusy, setStarBusy] = useState(false);

  // 关系编辑
  const [editingId, setEditingId] = useState(null);
  const [editType, setEditType] = useState('');
  const [editDesc, setEditDesc] = useState('');

  useEffect(() => {
    if (!entityId) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .entityDetail(entityId)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setType(data.entity.type);
        setRelTarget('');
        setRelDesc('');
        setRelType('related_to');
        setEditingId(null);
      })
      .catch((err) => setError(err.message));
    return () => {
      alive = false;
    };
  }, [entityId]);

  const saveType = async () => {
    try {
      await api.updateEntity(entityId, { type, status: 'confirmed' });
      setError('');
      onRefresh?.();
    } catch (err) {
      setError(err.message);
    }
  };

  const toggleStar = async () => {
    if (!detail) return;
    setStarBusy(true);
    setError('');
    try {
      const updated = await api.toggleStar(entityId, !detail.entity.starred);
      setDetail((prev) => (prev ? { ...prev, entity: updated } : prev));
      onRefresh?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setStarBusy(false);
    }
  };

  const targetOptions = nodes.filter((n) => n.id !== entityId);
  const neighborMap = Object.fromEntries((detail?.neighbors || []).map((n) => [n.id, n.name]));

  const otherIdOf = (edge) => (edge.source === entityId ? edge.target : edge.source);
  const otherNameOf = (edge) => {
    const otherId = otherIdOf(edge);
    return neighborMap[otherId] || `#${otherId}`;
  };

  const addRelation = async (event) => {
    event.preventDefault();
    if (!relTarget) {
      setError('请选择目标实体');
      return;
    }
    const targetId = Number(relTarget);
    setRelBusy(true);
    setError('');
    try {
      await api.createRelation({
        source_id: entityId,
        target_id: targetId,
        relation_type: relType,
        description: relDesc.trim(),
      });
      setRelDesc('');
      setRelTarget('');
      onRefresh?.();
      onSelectNode?.(targetId);   // 定位到新连接的实体,图谱自动高亮这条新关系
    } catch (err) {
      setError(err.message);
    } finally {
      setRelBusy(false);
    }
  };

  const startEdit = (edge) => {
    setEditingId(edge.id);
    setEditType(edge.relation_type);
    setEditDesc(edge.description || '');
  };

  const saveEdge = async (edgeId) => {
    setRelBusy(true);
    setError('');
    try {
      await api.updateRelation(edgeId, { relation_type: editType, description: editDesc.trim() });
      setEditingId(null);
      const updated = await api.entityDetail(entityId);
      setDetail(updated);
      onRefresh?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setRelBusy(false);
    }
  };

  const removeRelation = async (edge) => {
    if (!window.confirm(`删除与「${otherNameOf(edge)}」的 ${edge.relation_type} 关系?可在顶部撤销。`)) return;
    setDelBusy(true);
    setError('');
    try {
      await api.deleteRelation(edge.id);
      const updated = await api.entityDetail(entityId);
      setDetail(updated);
      onRefresh?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setDelBusy(false);
    }
  };

  const removeEntity = async () => {
    if (!window.confirm(`删除实体「${detail.entity.name}」?它关联的所有关系会一并删除。可在顶部撤销。`)) return;
    setDelBusy(true);
    setError('');
    try {
      await api.deleteEntity(entityId);
      onSelectNode?.(null);
      onRefresh?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setDelBusy(false);
    }
  };

  if (!entityId) {
    return (
      <div className="panel-body">
        <p className="hint">点击星图上的节点,查看它关联的文档片段</p>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="panel-body">
        {error ? <p className="error">{error}</p> : <p className="hint">加载中…</p>}
      </div>
    );
  }

  return (
    <div className="panel-body">
      <div className="detail-row">
        <h3 style={{ margin: 0, flex: 1 }}>{detail.entity.name}</h3>
        <button
          className={`star-btn${detail.entity.starred ? ' on' : ''}`}
          disabled={starBusy}
          title={detail.entity.starred ? '取消关注' : '加入我的关注'}
          onClick={toggleStar}
        >
          {detail.entity.starred ? '★ 已关注' : '☆ 关注'}
        </button>
      </div>
      <div className="detail-row">
        <select value={type} onChange={(event) => setType(event.target.value)}>
          {['concept', 'method', 'model', 'paper', 'person', 'tool', 'dataset', 'org'].map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
        <button onClick={saveType}>保存类型</button>
      </div>
      {detail.entity.description && <p className="hint">{detail.entity.description}</p>}

      {detail.neighbors?.length > 0 && (
        <>
          <h4>邻居 ({detail.neighbors.length})</h4>
          <div className="chip-row">
            {detail.neighbors.map((node) => (
              <button className="chip chip-button" key={node.id} onClick={() => onSelectNode(node.id)}>
                {node.name}
              </button>
            ))}
          </div>
        </>
      )}

      <h4>支撑证据 ({detail.evidence.length})</h4>
      <ul className="evidence-list">
        {detail.evidence.map((item) => (
          <li key={item.id}>
            <div className="evidence-source">{item.document_title} · {item.locator}</div>
            <div className="evidence-quote">{item.quote}</div>
          </li>
        ))}
      </ul>
      {!detail.evidence.length && <p className="hint">暂无证据片段(人工新增的实体/关系没有原文出处)</p>}

      <h4>关联关系 ({detail.edges.length})</h4>
      {!detail.edges.length && <p className="hint">这个实体还没有任何关系</p>}
      {detail.edges.map((edge) => (
        <div key={edge.id}>
          <div className="edge-row">
            <span className="edge-text">
              <span className="tag">{edge.relation_type}</span>
              {' → '}
              {otherNameOf(edge)}
              {edge.description && <span className="hint"> · {edge.description}</span>}
            </span>
            <div className="edge-actions">
              <button title="在星图中定位并高亮" onClick={() => onSelectNode(otherIdOf(edge))}>
                定位
              </button>
              <button onClick={() => startEdit(edge)}>编辑</button>
              <button className="danger" disabled={delBusy} onClick={() => removeRelation(edge)}>
                删除
              </button>
            </div>
          </div>
          {editingId === edge.id && (
            <form
              className="rel-form edge-edit"
              onSubmit={(event) => {
                event.preventDefault();
                saveEdge(edge.id);
              }}
            >
              <div className="detail-row">
                <select value={editType} onChange={(event) => setEditType(event.target.value)}>
                  {RELATION_TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              <input
                placeholder="关系描述"
                value={editDesc}
                onChange={(event) => setEditDesc(event.target.value)}
              />
              <div className="edge-actions">
                <button type="submit" disabled={relBusy}>保存</button>
                <button type="button" className="ghost" onClick={() => setEditingId(null)}>取消</button>
              </div>
            </form>
          )}
        </div>
      ))}

      <h4>新增关系</h4>
      {targetOptions.length === 0 ? (
        <p className="hint">图谱中还没有其他实体可连接,先点图谱上的「+ 实体」</p>
      ) : (
        <form className="rel-form" onSubmit={addRelation}>
          <div className="detail-row">
            <select value={relTarget} onChange={(event) => setRelTarget(event.target.value)}>
              <option value="">选择目标实体…</option>
              {targetOptions.map((n) => (
                <option key={n.id} value={n.id}>{n.name}（{n.type}）</option>
              ))}
            </select>
            <select value={relType} onChange={(event) => setRelType(event.target.value)}>
              {RELATION_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <input
            placeholder="关系描述(可选)"
            value={relDesc}
            onChange={(event) => setRelDesc(event.target.value)}
          />
          <button type="submit" disabled={relBusy || !relTarget}>添加关系</button>
        </form>
      )}

      <h4>危险操作</h4>
      <button className="danger" disabled={delBusy} onClick={removeEntity}>
        删除实体「{detail.entity.name}」
      </button>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
