import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function QAPanel({ spaceId = null, spaceName = '' }) {
  const [question, setQuestion] = useState('');
  const [hops, setHops] = useState(2);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 问答历史
  const [records, setRecords] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);

  const loadRecords = async () => {
    try {
      setRecords(await api.listRecords());
    } catch {
      /* 历史加载失败不影响提问 */
    }
  };

  useEffect(() => {
    loadRecords();
  }, []);

  const ask = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError('');
    try {
      const data = await api.ask(question.trim(), hops, spaceId);
      setResult(data);
      await loadRecords();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const toggleRecord = async (recordId) => {
    if (openId === recordId) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setError('');
    try {
      const data = await api.getRecord(recordId);
      setDetail(data);
      setOpenId(recordId);
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="panel-body">
      <h3>跨文档提问</h3>
      <p className="hint">
        当前空间:{spaceName || '全部'} · 是否只在这个空间里检索,由「设置」中的问答范围决定
      </p>
      <textarea
        rows={3}
        placeholder="例如:这些资料里,哪些方法是在 Transformer 基础上改进的?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <div className="qa-controls">
        <label>
          跳数
          <select value={hops} onChange={(event) => setHops(Number(event.target.value))}>
            <option value={1}>1 跳</option>
            <option value={2}>2 跳</option>
            <option value={3}>3 跳</option>
          </select>
        </label>
        <button disabled={loading || !question.trim()} onClick={ask}>
          {loading ? '检索中…' : '提问'}
        </button>
      </div>
      {error && <p className="error">{error}</p>}

      {result && (
        <div className="qa-result">
          <div className="qa-answer">
            {result.model_available ? null : <span className="tag tag-pending">离线兜底</span>}
            <p>{result.answer}</p>
          </div>

          {result.concepts?.length > 0 && (
            <div className="qa-block">
              <h4>关键概念</h4>
              <div className="chip-row">
                {result.concepts.map((concept) => (
                  <span className="chip" key={concept}>{concept}</span>
                ))}
              </div>
            </div>
          )}

          {result.paths?.length > 0 && (
            <div className="qa-block">
              <h4>关系路径 (程序 BFS)</h4>
              <ul className="path-list">
                {result.paths.slice(0, 10).map((path, index) => (
                  <li key={index}>{path.join(' → ')}</li>
                ))}
              </ul>
            </div>
          )}

          {result.evidence?.length > 0 && (
            <div className="qa-block">
              <h4>原文证据</h4>
              <ul className="evidence-list">
                {result.evidence.map((item) => (
                  <li key={item.id}>
                    <div className="evidence-source">
                      {item.document_title} · {item.locator}
                    </div>
                    <div className="evidence-quote">{item.quote}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <h4>问答历史 ({records.length})</h4>
      {!records.length && <p className="hint">还没有提问记录</p>}
      <ul className="review-list">
        {records.map((record) => (
          <li key={record.id}>
            <span className="rel-text">{record.question}</span>
            <span className="tag">
              {record.model_available === 'true' ? 'AI' : '离线'}
            </span>
            <div className="doc-actions">
              <button onClick={() => toggleRecord(record.id)}>
                {openId === record.id ? '收起' : '查看'}
              </button>
            </div>
            {openId === record.id && detail && (
              <div className="qa-result record-detail">
                <p>{detail.answer}</p>
                {detail.concepts?.length > 0 && (
                  <div className="qa-block">
                    <h4>关键概念</h4>
                    <div className="chip-row">
                      {detail.concepts.map((concept) => (
                        <span className="chip" key={concept}>{concept}</span>
                      ))}
                    </div>
                  </div>
                )}
                {detail.paths?.length > 0 && (
                  <div className="qa-block">
                    <h4>关系路径</h4>
                    <ul className="path-list">
                      {detail.paths.slice(0, 10).map((path, index) => (
                        <li key={index}>{path.join(' → ')}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {detail.evidence?.length > 0 && (
                  <div className="qa-block">
                    <h4>原文证据</h4>
                    <ul className="evidence-list">
                      {detail.evidence.map((item) => (
                        <li key={item.id}>
                          <div className="evidence-source">
                            {item.document_title} · {item.locator}
                          </div>
                          <div className="evidence-quote">{item.quote}</div>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
