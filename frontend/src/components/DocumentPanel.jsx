import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { useChoose, useConfirm } from './Confirm.jsx';
import { useToast } from './Toast.jsx';

const STATUS_TEXT = {
  pending: '待处理',
  parsed: '已分块',
  extracted: '已抽取',
  failed: '失败',
};

/** 把秒数说成人话:80 → 「1 分 20 秒」。 */
function fmtDuration(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

/** 抽取进度:已抽取块数 + 已用时间 + 预计剩余时间。 */
function ExtractProgress({ job }) {
  const percent = job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <div className="extract-progress">
      <div className="extract-bar">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="extract-meta">
        <span>已抽取 {job.done}/{job.total} 块</span>
        <span>已用时 {fmtDuration(job.elapsed)}</span>
        <span>预计还需 {fmtDuration(job.eta)}</span>
      </div>
    </div>
  );
}

export default function DocumentPanel({ documents, spaces = [], onChange, onToast }) {
  const toast = useToast();
  const confirm = useConfirm();
  const choose = useChoose();

  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);

  // 抽取进度:extracting 记有哪些文档在跑,jobs 是后端回报的实时进度
  const [extracting, setExtracting] = useState([]);
  const [jobs, setJobs] = useState({});

  // 片段浏览
  const [chunksFor, setChunksFor] = useState(null);
  const [chunkData, setChunkData] = useState(null);
  const [chunkBusy, setChunkBusy] = useState(false);

  // 抽取期间每秒拉一次进度。这个接口只读内存、不查库,不会和抽取抢数据库锁
  useEffect(() => {
    if (!extracting.length) {
      setJobs({});
      return undefined;
    }
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.documentProgress();
        if (alive) setJobs(data.jobs || {});
      } catch (_) {
        /* 进度只是锦上添花,拉取失败不该打断正在进行的抽取 */
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [extracting]);

  /** 预计耗时偏长时,先问用户要完整抽取还是快速抽取。返回 'full' / 'fast' / null。 */
  const askMode = async (docs) => {
    const fullTotal = docs.reduce((sum, doc) => sum + (doc.est_seconds || 0), 0);
    const fastTotal = docs.reduce((sum, doc) => sum + (doc.fast_seconds || 0), 0);
    const lines = docs.map(
      (doc) => `《${doc.title}》${doc.chunk_count} 块 · 完整抽取约 ${fmtDuration(doc.est_seconds)}`
    );
    return choose({
      title: docs.length > 1 ? `${docs.length} 篇文档预计耗时较长` : '这篇文档预计耗时较长',
      message: `${lines.join('\n')}\n\n完整抽取合计约 ${fmtDuration(fullTotal)},快速抽取约 ${fmtDuration(fastTotal)}。`,
      options: [
        {
          value: 'full',
          label: `完整抽取(约 ${fmtDuration(fullTotal)})`,
          description: '所有片段都交给模型,实体与关系最完整',
        },
        {
          value: 'fast',
          label: `快速抽取(约 ${fmtDuration(fastTotal)})`,
          description: '每篇只抽前面一部分片段,先快速看效果',
        },
      ],
    });
  };

  /** 抽取一篇文档,返回是否成功。失败原因在这里统一提示。 */
  const runExtract = async (doc, fast) => {
    setExtracting((prev) => (prev.includes(doc.id) ? prev : [...prev, doc.id]));
    try {
      await api.extractDocument(doc.id, fast);
      return true;
    } catch (err) {
      toast.error(`《${doc.title}》抽取失败:${err.message || '未知原因'}`);
      return false;
    } finally {
      setExtracting((prev) => prev.filter((id) => id !== doc.id));
      // 成败都刷新一次:失败时要把失败状态和原因显示出来
      await onChange();
    }
  };

  /** 逐篇抽取:短的直接抽,预计偏长的先问用户。返回完成抽取的篇数。 */
  const extractAll = async (docs) => {
    const usable = docs.filter((doc) => doc.status !== 'failed');
    const heavy = usable.filter((doc) => doc.heavy);
    const light = usable.filter((doc) => !doc.heavy);

    let count = 0;
    for (const doc of light) {
      if (await runExtract(doc, false)) count += 1;
    }

    if (heavy.length) {
      const mode = await askMode(heavy);
      if (!mode) {
        toast.info(
          `${heavy.length} 篇文档已保存为「已分块」,想抽的时候点「抽取」即可`
        );
      } else {
        for (const doc of heavy) {
          if (await runExtract(doc, mode === 'fast')) count += 1;
        }
      }
    }
    return count;
  };

  const upload = async (files) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      const uploaded = [];
      for (const file of files) {
        const doc = await api.uploadDocument(file);
        uploaded.push(doc);
        if (doc.status === 'failed') {
          toast.error(`《${doc.title}》解析失败:${doc.error || '未知原因'}`);
        }
      }
      const done = await extractAll(uploaded);
      await onChange();
      toast.success(`已上传 ${uploaded.length} 篇文档,${done} 篇完成抽取`);
    } catch (err) {
      toast.error(err.message || '上传失败');
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const extract = async (doc) => {
    setBusy(true);
    try {
      if (doc.heavy) {
        const mode = await askMode([doc]);
        if (!mode) return;
        if (await runExtract(doc, mode === 'fast')) {
          toast.success(`《${doc.title}》抽取完成`);
        }
        return;
      }
      if (await runExtract(doc, false)) toast.success(`《${doc.title}》抽取完成`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (doc) => {
    const ok = await confirm({
      title: '删除文档',
      message: `删除《${doc.title}》会一并移除它的片段与相关证据;失去证据支撑的实体也会被清理。`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteDocument(doc.id);
      if (chunksFor === doc.id) {
        setChunksFor(null);
        setChunkData(null);
      }
      await onChange();
      toast.success(`已删除《${doc.title}》`);
    } catch (err) {
      toast.error(err.message || '删除失败');
    } finally {
      setBusy(false);
    }
  };

  const setSpace = async (doc, spaceId) => {
    try {
      await api.setDocumentSpace(doc.id, spaceId);
      await onChange();
    } catch (err) {
      onToast?.(err.message || '归类失败');
    }
  };

  const toggleChunks = async (id) => {
    if (chunksFor === id) {
      setChunksFor(null);
      setChunkData(null);
      return;
    }
    setChunkBusy(true);
    try {
      setChunkData(await api.listChunks(id));
      setChunksFor(id);
    } catch (err) {
      toast.error(err.message || '片段加载失败');
    } finally {
      setChunkBusy(false);
    }
  };

  return (
    <section className="panel">
      <h2>文档</h2>

      <div
        className={`dropzone${dragging ? ' dragging' : ''}${busy ? ' busy' : ''}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          upload(Array.from(event.dataTransfer?.files || []));
        }}
      >
        {busy ? '正在上传并抽取…' : '拖拽文件到此处,或点击选择'}
      </div>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        accept=".pdf,.md,.markdown,.txt"
        onChange={(event) => upload(Array.from(event.target.files || []))}
      />
      <p className="hint">支持 PDF / Markdown / TXT,建议一次上传同一主题的 5~10 篇</p>

      <ul className="doc-list">
        {documents.map((doc) => {
          const job = jobs[doc.id];
          const running = extracting.includes(doc.id);
          return (
            <li key={doc.id}>
              <div className="doc-title" title={doc.filename}>
                {doc.title}
              </div>
              <div className="doc-meta">
                <span className={`tag tag-${doc.status}`}>
                  {STATUS_TEXT[doc.status] || doc.status}
                </span>
                <span>{doc.chunk_count} 块</span>
                {doc.status !== 'extracted' && doc.est_seconds > 0 && (
                  <span>预计约 {fmtDuration(doc.est_seconds)}</span>
                )}
              </div>
              <select
                className="doc-space"
                value={doc.space_id ?? ''}
                title="把这篇文档归入某个知识空间"
                onChange={(event) =>
                  setSpace(doc, event.target.value === '' ? null : Number(event.target.value))
                }
              >
                <option value="">未归档</option>
                {spaces.map((space) => (
                  <option key={space.id} value={space.id}>
                    {space.name}
                  </option>
                ))}
              </select>
              {job ? (
                <ExtractProgress job={job} />
              ) : (
                running && (
                  <div className="extract-meta">
                    <span>正在抽取…</span>
                  </div>
                )
              )}
              {doc.error && <p className="error">{doc.error}</p>}
              <div className="doc-actions">
                <button disabled={busy || chunkBusy} onClick={() => toggleChunks(doc.id)}>
                  {chunksFor === doc.id ? '收起片段' : '片段'}
                </button>
                <button disabled={busy} onClick={() => extract(doc)}>
                  {doc.status === 'extracted' ? '重抽' : '抽取'}
                </button>
                <button className="danger" disabled={busy} onClick={() => remove(doc)}>
                  删除
                </button>
              </div>

              {chunksFor === doc.id && chunkData && (
                <ul className="chunk-list">
                  {chunkData.chunks.map((chunk) => (
                    <li key={chunk.id}>
                      <div className="chunk-locator">
                        {chunk.locator || `第 ${chunk.chunk_index + 1} 段`}
                      </div>
                      <div className="chunk-content">{chunk.content}</div>
                    </li>
                  ))}
                  {!chunkData.chunks.length && <li className="hint">这篇文档还没有片段</li>}
                </ul>
              )}
            </li>
          );
        })}
        {!documents.length && <li className="hint">还没有上传任何文档</li>}
      </ul>
    </section>
  );
}
