import { useEffect, useState } from 'react';
import { api } from '../api.js';

const ENTITY_TYPE_OPTIONS = [
  'method', 'model', 'paper', 'person', 'tool', 'dataset', 'concept', 'org',
];

const RELATION_TYPE_OPTIONS = [
  'related_to', 'derives_from', 'improves_on',
  'part_of', 'uses', 'compared_with', 'proposed_by',
];

/** 个性化设置:问答偏好 + 自定义抽取维度。 */
export default function SettingsPanel({ onClose, onToast }) {
  const [settings, setSettings] = useState(null);
  const [focusDraft, setFocusDraft] = useState('');

  useEffect(() => {
    api
      .getSettings()
      .then((data) => {
        setSettings(data);
        setFocusDraft(data.extract_focus || '');
      })
      .catch((err) => onToast?.(err.message));
  }, []);

  const toggle = (value, list) =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const save = async (patch) => {
    // 先本地更新,保证点选即时反馈;失败再回滚并提示
    const previous = settings;
    setSettings({ ...settings, ...patch });
    try {
      setSettings(await api.updateSettings(patch));
    } catch (err) {
      setSettings(previous);
      onToast?.(err.message);
    }
  };

  if (!settings) {
    return (
      <div className="auth-card">
        <h2>个性化设置</h2>
        <p className="hint">加载中…</p>
      </div>
    );
  }

  return (
    <div className="auth-card settings-card">
      <h2>个性化设置</h2>
      <p className="hint">这些偏好会作用在此后每一次提问上。</p>

      <div className="settings-row">
        <span>回答风格</span>
        <div className="segmented">
          <button
            className={settings.answer_style === 'concise' ? 'active' : ''}
            onClick={() => save({ answer_style: 'concise' })}
          >
            简洁
          </button>
          <button
            className={settings.answer_style === 'detailed' ? 'active' : ''}
            onClick={() => save({ answer_style: 'detailed' })}
          >
            详尽
          </button>
        </div>
      </div>

      <div className="settings-row">
        <span>回答语言</span>
        <div className="segmented">
          <button
            className={settings.answer_language === 'zh' ? 'active' : ''}
            onClick={() => save({ answer_language: 'zh' })}
          >
            中文
          </button>
          <button
            className={settings.answer_language === 'en' ? 'active' : ''}
            onClick={() => save({ answer_language: 'en' })}
          >
            English
          </button>
        </div>
      </div>

      <div className="settings-row">
        <span>引用原文证据</span>
        <div className="segmented">
          <button
            className={settings.cite_evidence ? 'active' : ''}
            onClick={() => save({ cite_evidence: true })}
          >
            逐条引用
          </button>
          <button
            className={settings.cite_evidence ? '' : 'active'}
            onClick={() => save({ cite_evidence: false })}
          >
            只概括
          </button>
        </div>
      </div>

      <div className="settings-row">
        <span>问答范围</span>
        <div className="segmented">
          <button
            className={settings.qa_scope === 'all' ? 'active' : ''}
            onClick={() => save({ qa_scope: 'all' })}
          >
            全部文档
          </button>
          <button
            className={settings.qa_scope === 'current_space' ? 'active' : ''}
            onClick={() => save({ qa_scope: 'current_space' })}
          >
            跟随当前空间
          </button>
        </div>
      </div>

      <p className="hint">
        {settings.qa_scope === 'current_space'
          ? '提问时只检索当前选中空间内的文档;未选空间时等同全部。'
          : '提问时始终检索全部文档,不受左侧空间选择影响。'}
      </p>

      <h4 className="settings-divider">自定义抽取维度</h4>
      <p className="hint">下次上传新文档或「重抽」时生效,已有的实体不会被删。</p>

      <div className="settings-block">
        <h5>实体类型</h5>
        <div className="filter-row">
          {ENTITY_TYPE_OPTIONS.map((type) => (
            <label key={type}>
              <input
                type="checkbox"
                checked={(settings.extract_entity_types || []).includes(type)}
                onChange={() =>
                  save({
                    extract_entity_types: toggle(
                      type,
                      settings.extract_entity_types || []
                    ),
                  })
                }
              />
              {type}
            </label>
          ))}
        </div>
        <p className="hint">一个都不选 = 不限制;勾选后只抽取这些类型。</p>
      </div>

      <div className="settings-block">
        <h5>关系类型</h5>
        <div className="filter-row">
          {RELATION_TYPE_OPTIONS.map((type) => (
            <label key={type}>
              <input
                type="checkbox"
                checked={(settings.extract_relation_types || []).includes(type)}
                onChange={() =>
                  save({
                    extract_relation_types: toggle(
                      type,
                      settings.extract_relation_types || []
                    ),
                  })
                }
              />
              {type}
            </label>
          ))}
        </div>
      </div>

      <div className="settings-block">
        <h5>关注领域</h5>
        <input
          placeholder="如:Transformer 架构演进 / 产品功能与用户痛点"
          value={focusDraft}
          onChange={(event) => setFocusDraft(event.target.value)}
        />
        <div className="entity-form-actions" style={{ marginTop: 6 }}>
          <button
            onClick={() => save({ extract_focus: focusDraft.trim() })}
            disabled={focusDraft.trim() === (settings.extract_focus || '')}
          >
            保存领域
          </button>
        </div>
        <p className="hint">会作为提示语传给抽取模型,让它优先关注这个方向。</p>
      </div>

      <div className="modal-actions">
        <button onClick={onClose}>完成</button>
      </div>
    </div>
  );
}
