import { useState } from 'react';
import { api, auth } from '../api.js';
import { useToast } from './Toast.jsx';

export default function AuthScreen({ onAuthed }) {
  const toast = useToast();
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (username.trim().length < 3) {
      toast.error('用户名至少 3 个字符');
      return;
    }
    if (password.length < 6) {
      toast.error('密码至少 6 个字符');
      return;
    }
    setBusy(true);
    try {
      const data =
        mode === 'register'
          ? await api.auth.register(username.trim(), password)
          : await api.auth.login(username.trim(), password);
      auth.setToken(data.access_token);
      onAuthed(data.user);
      toast.success(mode === 'register' ? '注册成功,已自动登录' : '登录成功');
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="brand">
          知识星图 <span className="sub">文档关系探索器</span>
        </div>
        <div className="auth-tabs">
          <button
            className={mode === 'login' ? 'active' : ''}
            onClick={() => setMode('login')}
          >
            登录
          </button>
          <button
            className={mode === 'register' ? 'active' : ''}
            onClick={() => setMode('register')}
          >
            注册
          </button>
        </div>
        <form onSubmit={submit}>
          <input
            placeholder="用户名(至少 3 位)"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
          <input
            type="password"
            placeholder="密码(至少 6 位)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
          />
          <button type="submit" disabled={busy}>
            {busy ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>
      </div>
    </div>
  );
}
