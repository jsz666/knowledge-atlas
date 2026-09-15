import { useState } from 'react';
import { api } from '../api.js';

/** 知识空间切换:选中后图谱、统计、搜索与问答都只在这个空间内进行。 */
export default function SpaceBar({ spaces, currentId, onSelect, onChange, onToast }) {
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [shareTokens, setShareTokens] = useState({});

  const create = async (event) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await api.createSpace({ name: trimmed });
      setName('');
      setCreating(false);
      await onChange?.();
    } catch (err) {
      onToast?.(err.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (space) => {
    const ok = window.confirm(
      `删除空间「${space.name}」?其中的文档不会被删除,只是回到「未归档」。`
    );
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteSpace(space.id);
      if (currentId === space.id) onSelect?.(null);
      await onChange?.();
    } catch (err) {
      onToast?.(err.message);
    } finally {
      setBusy(false);
    }
  };

  const share = async (space) => {
    try {
      const result = await api.shareSpace(space.id);
      setShareTokens((prev) => ({ ...prev, [space.id]: result.token }));
      const link = `${window.location.origin}/?share=${result.token}`;
      try {
        await navigator.clipboard.writeText(link);
        onToast?.('分享链接已复制,对方无需登录即可查看', 'success');
      } catch (_) {
        window.prompt('复制这个只读分享链接:', link);
      }
    } catch (err) {
      onToast?.(err.message);
    }
  };

  const revoke = async (space) => {
    const ok = window.confirm(`撤销「${space.name}」的分享?已经发出去的链接会立即失效。`);
    if (!ok) return;
    try {
      await api.revokeShare(space.id);
      setShareTokens((prev) => {
        const next = { ...prev };
        delete next[space.id];
        return next;
      });
      onToast?.('已撤销分享', 'success');
    } catch (err) {
      onToast?.(err.message);
    }
  };

  return (
    <section className="panel space-panel">
      <h2>知识空间</h2>

      <div className="space-list">
        <button
          className={`space-chip${currentId == null ? ' active' : ''}`}
          onClick={() => onSelect?.(null)}
        >
          全部
        </button>

        {spaces.map((space) => (
          <div key={space.id} className={`space-item${currentId === space.id ? ' active' : ''}`}>
            <button
              className="space-chip"
              title={space.description || space.name}
              onClick={() => onSelect?.(space.id)}
            >
              <span className="space-dot" style={{ background: space.color }} />
              {space.name}
              <span className="space-count">
                {space.document_count} 篇 · {space.entity_count} 实体
              </span>
            </button>
            <button
              className="space-act"
              disabled={busy}
              title="复制只读分享链接(对方无需登录)"
              onClick={() => share(space)}
            >
              分享
            </button>
            {shareTokens[space.id] && (
              <button
                className="space-act"
                disabled={busy}
                title="撤销分享"
                onClick={() => revoke(space)}
              >
                取消
              </button>
            )}
            <button
              className="space-del"
              disabled={busy}
              title="删除空间"
              onClick={() => remove(space)}
            >
              ×
            </button>
          </div>
        ))}

        {!spaces.length && !creating && (
          <p className="hint">还没有空间。新建后把文档归进去,就能按主题分别看图谱。</p>
        )}
      </div>

      {creating ? (
        <form className="space-form" onSubmit={create}>
          <input
            placeholder="空间名称,如「毕业论文」"
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
          />
          <div className="entity-form-actions">
            <button type="submit" disabled={busy || !name.trim()}>创建</button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setCreating(false);
                setName('');
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <button className="ghost space-add" onClick={() => setCreating(true)}>+ 新建空间</button>
      )}
    </section>
  );
}
