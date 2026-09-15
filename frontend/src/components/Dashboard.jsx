import { useEffect, useState } from 'react';
import { api } from '../api.js';

/** 只取日期部分,避免展示一长串时间戳。 */
const fmtDate = (value) => (value ? String(value).slice(0, 10) : '');

export default function Dashboard({ data, onSelectNode, onRefresh, onGoReview, onToast }) {
  const [busyId, setBusyId] = useState(null);
  const [insight, setInsight] = useState(null);
  const [gaps, setGaps] = useState(null);

  // 每日洞察按天缓存;缺口是纯统计,随手算
  useEffect(() => {
    api.getInsight().then(setInsight).catch(() => setInsight(null));
    api.getGaps().then(setGaps).catch(() => setGaps(null));
  }, []);

  if (!data) {
    return (
      <div className="panel-body">
        <p className="hint">正在加载你的主页…</p>
      </div>
    );
  }

  const { user, stats = {}, joined_days: joinedDays = 1 } = data;
  const starred = data.starred_entities || [];
  const top = data.top_entities || [];
  const documents = data.recent_documents || [];
  const questions = data.recent_qa || [];
  const pending = (stats.pending_entities || 0) + (stats.pending_relations || 0);

  const toggleStar = async (entity) => {
    setBusyId(entity.id);
    try {
      await api.toggleStar(entity.id, !entity.starred);
      await onRefresh?.();
    } catch (err) {
      onToast?.(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const starButton = (entity) => (
    <button
      className={`star-btn${entity.starred ? ' on' : ''}`}
      disabled={busyId === entity.id}
      title={entity.starred ? '取消关注' : '加入我的关注'}
      onClick={() => toggleStar(entity)}
    >
      {entity.starred ? '★' : '☆'}
    </button>
  );

  return (
    <div className="panel-body">
      <div className="dash-head">
        <h3>你好,{user.username}</h3>
        <span className="hint">
          加入第 {joinedDays} 天 · 这是只属于你的知识星图
        </span>
      </div>

      {insight && (
        <div className="dash-section insight-card">
          <h4>今日洞察</h4>
          <p className="insight-text">{insight.content}</p>
        </div>
      )}

      <div className="dash-cards">
        <div className="dash-card">
          <span className="num">{stats.documents ?? 0}</span>
          <span className="label">文档</span>
        </div>
        <div className="dash-card">
          <span className="num">{stats.entities ?? 0}</span>
          <span className="label">实体</span>
        </div>
        <div className="dash-card">
          <span className="num">{stats.relations ?? 0}</span>
          <span className="label">关系</span>
        </div>
        <div className="dash-card">
          <span className="num">{stats.cross_doc_entities ?? 0}</span>
          <span className="label">跨文档实体</span>
        </div>
        <div className="dash-card warn">
          <span className="num">{pending}</span>
          <span className="label">待确认</span>
        </div>
        <div className="dash-card warn">
          <span className="num">{stats.starred_entities ?? 0}</span>
          <span className="label">我的关注</span>
        </div>
      </div>

      {pending > 0 && (
        <div className="dash-section">
          <h4>待办</h4>
          <p className="dash-empty">
            还有 {stats.pending_entities || 0} 个实体、{stats.pending_relations || 0} 条关系等待确认。
            <button onClick={onGoReview}>去确认</button>
          </p>
        </div>
      )}

      <div className="dash-section">
        <h4>我的关注 ({starred.length})</h4>
        {starred.length ? (
          <ul className="dash-list">
            {starred.map((entity) => (
              <li key={entity.id}>
                <button className="chip chip-button" onClick={() => onSelectNode?.(entity.id)}>
                  {entity.name}
                </button>
                <span className="meta">{entity.type} · 关系 {entity.degree}</span>
                {starButton(entity)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="dash-empty">
            还没有关注任何实体。在「节点详情」或下面的热门实体里点 ☆,就能把它固定在这里。
          </p>
        )}
      </div>

      <div className="dash-section">
        <h4>热门实体</h4>
        {top.length ? (
          <ul className="dash-list">
            {top.map((entity) => (
              <li key={entity.id}>
                <button className="chip chip-button" onClick={() => onSelectNode?.(entity.id)}>
                  {entity.name}
                </button>
                <span className="meta">
                  关系 {entity.degree} · 跨 {entity.doc_count} 篇
                </span>
                {starButton(entity)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="dash-empty">还没有实体,先上传一篇文档试试。</p>
        )}
      </div>

      <div className="dash-section">
        <h4>最近上传</h4>
        {documents.length ? (
          <ul className="dash-list">
            {documents.map((doc) => (
              <li key={doc.id}>
                <span>{doc.title}</span>
                <span className="meta">{doc.file_type} · {fmtDate(doc.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="dash-empty">还没有上传过文档。</p>
        )}
      </div>

      <div className="dash-section">
        <h4>最近提问</h4>
        {questions.length ? (
          <ul className="dash-list">
            {questions.map((item) => (
              <li key={item.id}>
                <span>{item.question}</span>
                <span className="meta">{fmtDate(item.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="dash-empty">还没有提问记录,去「跨文档问答」试试。</p>
        )}
      </div>

      {gaps && (
        <div className="dash-section">
          <h4>知识缺口</h4>
          {!gaps.isolated.length &&
          !gaps.weak_evidence.length &&
          !gaps.missing_links.length ? (
            <p className="dash-empty">暂时没有明显缺口,图谱连接得不错。</p>
          ) : (
            <>
              {gaps.isolated.length > 0 && (
                <>
                  <p className="dash-empty">有原文支撑,但还没有任何关系:</p>
                  <div className="chip-row">
                    {gaps.isolated.map((item) => (
                      <button
                        key={`iso-${item.id}`}
                        className="chip chip-button"
                        onClick={() => onSelectNode?.(item.id)}
                      >
                        {item.name}
                      </button>
                    ))}
                  </div>
                </>
              )}

              {gaps.weak_evidence.length > 0 && (
                <>
                  <p className="dash-empty" style={{ marginTop: 8 }}>
                    只在一篇文档里出现过,证据偏弱:
                  </p>
                  <div className="chip-row">
                    {gaps.weak_evidence.map((item) => (
                      <button
                        key={`weak-${item.id}`}
                        className="chip chip-button"
                        onClick={() => onSelectNode?.(item.id)}
                      >
                        {item.name}
                      </button>
                    ))}
                  </div>
                </>
              )}

              {gaps.missing_links.length > 0 && (
                <>
                  <p className="dash-empty" style={{ marginTop: 8 }}>
                    经常一起出现,但彼此之间还没有关系:
                  </p>
                  <ul className="dash-list">
                    {gaps.missing_links.map((link) => (
                      <li key={`link-${link.source_id}-${link.target_id}`}>
                        <span>{link.source} · {link.target}</span>
                        <span className="meta">共现 {link.co_occurrences} 次</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
