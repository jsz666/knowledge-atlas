import { useState } from 'react';
import { api } from '../api.js';

/** 登录 / 注册面板:未登录时展示,登录成功后把令牌交给上层。 */

export default function AuthPanel({ mode, onModeChange, onAuthed, onToast }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const isRegister = mode === 'register';

  const submit = async (event) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('请填写用户名和密码');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = isRegister
        ? await api.register(username.trim(), password)
        : await api.login(username.trim(), password);
      onAuthed(result.user, result.token, {
        claimed: result.claimed_orphans || 0,
        registered: isRegister,
      });
    } catch (err) {
      const message = err.message || '操作失败';
      setError(message);
      if (onToast) onToast(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-card">
      <h2>{isRegister ? '创建账号' : '登录'}</h2>
      <p className="hint">
        {isRegister
          ? '每个账号拥有独立的文档与知识图谱;注册后可上传文档或载入示例。'
          : '登录后可以上传文档、确认实体、合并与撤销删除。不登录也能浏览,但看不到任何人的私有数据。'}
      </p>

      <form onSubmit={submit}>
        <label className="auth-field">
          <span>用户名</span>
          <input
            value={username}
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            placeholder="至少 2 个字符"
          />
        </label>
        <label className="auth-field">
          <span>密码</span>
          <input
            type="password"
            value={password}
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="至少 6 位"
          />
        </label>

        {error && <p className="error">{error}</p>}

        <button type="submit" disabled={busy}>
          {busy ? '处理中…' : isRegister ? '注册并登录' : '登录'}
        </button>
      </form>

      <div className="auth-switch">
        {isRegister ? '已有账号?' : '还没有账号?'}
        <button
          type="button"
          className="ghost link"
          onClick={() => {
            setError('');
            onModeChange(isRegister ? 'login' : 'register');
          }}
        >
          {isRegister ? '去登录' : '去注册'}
        </button>
      </div>
    </div>
  );
}
