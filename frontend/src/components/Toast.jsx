import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

/** 轻量通知:替代 window.alert,操作结果统一在右下角提示,可自动消失。 */

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const seq = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const push = useCallback(
    (message, type = 'info', duration = 3200) => {
      seq.current += 1;
      const id = seq.current;
      setToasts((prev) => [...prev.slice(-4), { id, message, type }]);
      if (duration > 0) {
        setTimeout(() => dismiss(id), duration);
      }
      return id;
    },
    [dismiss]
  );

  const toast = useMemo(
    () => ({
      success: (message, duration) => push(message, 'success', duration),
      error: (message, duration) => push(message, 'error', duration ?? 5200),
      info: (message, duration) => push(message, 'info', duration),
      dismiss,
    }),
    [push, dismiss]
  );

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((item) => (
          <div key={item.id} className={`toast toast-${item.type}`}>
            <span className="toast-msg">{item.message}</span>
            <button
              type="button"
              className="toast-close"
              aria-label="关闭提示"
              onClick={() => dismiss(item.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast 必须在 ToastProvider 内使用');
  }
  return context;
}
