import { useEffect, useState } from 'react';
import { api } from '../api.js';

// 与后端 config.RELATION_TYPES 保持一致
const RELATION_TYPES = [
  'related_to', 'derives_from', 'improves_on',
  'part_of', 'uses', 'compared_with', 'proposed_by',
];

/**
 * 人工确认面板:AI 抽出来的实体与关系默认是 pending,
 * 必须在这里确认、改名或删除,才算真正进入知识星图。
 * 另外提供实体消歧:合并归一化后同名的重复实体。
 */
export default function ReviewPanel({ refreshKey, nameMap = {}, onChange }) {
  const [queue, setQueue] = useState({ entities: [], relations: [] });
  const [entityNames, setEntityNames] = useState({});
  const [relationTypes, setRelationTypes] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // 实体消歧
  const [dupGroups, setDupGroups] = useState([]);
  const [keepChoice, setKeepChoice] = useState({});
  const [mergeBusy, setMergeBusy] = useState(false);
  const [mergeNote, setMergeNote] = useState('');

  const load = async () => {
    try {
      const data = await api.reviewQueue();
      setQueue(data);
      setEntityNames(Object.fromEntries(data.entities.map((e) => [e.id, e.name])));
      setRelationTypes(Object.fromEntries(data.relations.map((r) => [r.id, r.relation_type])));

      const dups = await api.listDuplicates();
      const groups = dups.groups || [];
      setDupGroups(groups);
      setKeepChoice(
        Object.fromEntries(groups.map((group) => [group.key, group.items[0].id]))
      );
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const act = async (fn) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      await load();
      await onChange?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const mergeGroup = async (group) => {
    const keepId = keepChoice[group.key] || group.items[0].id;
    const others = group.items.filter((item) => item.id !== keepId);
    if (!others.length) return;

    const keepName = group.items.find((item) => item.id === keepId)?.name;
    const ok = window.confirm(
      `把「${others.map((item) => item.name).join('、')}」合并进「${keepName}」?\n` +
        '关系与证据会迁移过去,此操作不可撤销(建议先导出快照备份)。'
    );
    if (!ok) return;

    setMergeBusy(true);
    setError('');
    setMergeNote('');
    try {
      const result = await api.mergeEntities(keepId, others.map((item) => item.id));
      await load();
      await onChange?.();
      const merged = result?.merged || {};
      setMergeNote(
        `已合并:实体 ${merged.merged_entities} 个、关系迁移 ${merged.relations} 条、` +
          `证据 ${merged.evidence} 条、去重丢弃 ${merged.dropped_relations} 条`
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setMergeBusy(false);
    }
  };

  return (
    <div className="panel-body">
      <h3>待确认实体 ({queue.entities.length})</h3>
      {!queue.entities.length && <p className="hint">没有待确认的实体,去上传文档或重新抽取吧</p>}
      <ul className="review-list">
        {queue.entities.map((entity) => (
          <li key={entity.id}>
            <input
              value={entityNames[entity.id] ?? entity.name}
              onChange={(event) =>
                setEntityNames({ ...entityNames, [entity.id]: event.target.value })
              }
            />
            <span className="tag">{entity.type}</span>
            <div className="doc-actions">
              <button
                disabled={busy}
                onClick={() =>
                  act(() =>
                    api.updateEntity(entity.id, {
                      name: entityNames[entity.id] ?? entity.name,
                      status: 'confirmed',
                    })
                  )
                }
              >
                确认
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => act(() => api.deleteEntity(entity.id))}
              >
                删除
              </button>
            </div>
          </li>
        ))}
      </ul>

      <h3>待确认关系 ({queue.relations.length})</h3>
      {!queue.relations.length && <p className="hint">没有待确认的关系</p>}
      <ul className="review-list">
        {queue.relations.map((relation) => (
          <li key={relation.id}>
            <span className="rel-text">
              {nameMap[relation.source_id] || `#${relation.source_id}`}
              {' → '}
              {nameMap[relation.target_id] || `#${relation.target_id}`}
            </span>
            <select
              className="rel-type"
              value={relationTypes[relation.id] ?? relation.relation_type}
              onChange={(event) =>
                setRelationTypes({ ...relationTypes, [relation.id]: event.target.value })
              }
            >
              {RELATION_TYPES.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <div className="doc-actions">
              <button
                disabled={busy}
                onClick={() =>
                  act(() =>
                    api.updateRelation(relation.id, {
                      relation_type: relationTypes[relation.id] ?? relation.relation_type,
                      status: 'confirmed',
                    })
                  )
                }
              >
                确认
              </button>
              <button
                className="danger"
                disabled={busy}
                onClick={() => act(() => api.updateRelation(relation.id, { status: 'rejected' }))}
              >
                拒绝
              </button>
            </div>
          </li>
        ))}
      </ul>

      <h3>实体消歧 ({dupGroups.length})</h3>
      <p className="hint">
        归一化后同名(忽略大小写、空格、标点与"模型/方法"等后缀)的实体会被归为一组,
        选择要保留的那个再合并,关系与证据会自动迁移过去。
      </p>
      {!dupGroups.length && <p className="hint">没有检测到重复实体</p>}
      {dupGroups.map((group) => (
        <div className="dup-group" key={group.key}>
          <div className="dup-items">
            {group.items.map((item) => (
              <label key={item.id}>
                <input
                  type="radio"
                  name={`dup-${group.key}`}
                  checked={(keepChoice[group.key] || group.items[0].id) === item.id}
                  onChange={() => setKeepChoice({ ...keepChoice, [group.key]: item.id })}
                />
                {item.name}
                <span className="tag">{item.type}</span>
                <span className="tag">{item.status}</span>
              </label>
            ))}
          </div>
          <div className="doc-actions">
            <button disabled={mergeBusy} onClick={() => mergeGroup(group)}>
              合并为选中项
            </button>
          </div>
        </div>
      ))}
      {mergeNote && <p className="hint">{mergeNote}</p>}

      {error && <p className="error">{error}</p>}
    </div>
  );
}
