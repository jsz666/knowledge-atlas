import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

/**
 * 确认 / 选择弹窗:替代 window.confirm,样式与整体一致。
 * - confirm(options) 返回 Promise<boolean>
 * - choose(options)  返回 Promise<值 或 null>(null 表示取消)
 * 两者共用同一个弹窗,同一时刻只会有一个处于打开状态。
 */

const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null);
  const resolver = useRef(null);

  const open = useCallback(
    (payload) =>
      new Promise((resolve) => {
        resolver.current = resolve;
        setState(payload);
      }),
    []
  );

  const confirm = useCallback(
    (options) => {
      const payload =
        typeof options === 'string'
          ? { title: '请确认', message: options, danger: false }
          : {
              title: options?.title || '请确认',
              message: options?.message || '',
              confirmText: options?.confirmText || '确定',
              danger: Boolean(options?.danger),
            };
      return open({ kind: 'confirm', ...payload });
    },
    [open]
  );

  const choose = useCallback(
    (options) =>
      open({
        kind: 'choose',
        title: options?.title || '请选择',
        message: options?.message || '',
        options: options?.options || [],
      }),
    [open]
  );

  const close = useCallback((result) => {
    setState(null);
    if (resolver.current) {
      resolver.current(result);
      resolver.current = null;
    }
  }, []);

  const value = useMemo(() => ({ confirm, choose }), [confirm, choose]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {state && (
        <div className="modal-mask" onClick={() => close(state.kind === 'choose' ? null : false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <h3>{state.title}</h3>
            <p className="modal-msg">{state.message}</p>
            {state.kind === 'choose' ? (
              <div className="modal-choices">
                {state.options.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={option.danger ? 'danger' : ''}
                    onClick={() => close(option.value)}
                  >
                    <span className="choice-label">{option.label}</span>
                    {option.description && (
                      <span className="choice-desc">{option.description}</span>
                    )}
                  </button>
                ))}
                <button type="button" className="ghost" onClick={() => close(null)}>
                  取消
                </button>
              </div>
            ) : (
              <div className="modal-actions">
                <button type="button" className="ghost" onClick={() => close(false)}>
                  取消
                </button>
                <button
                  type="button"
                  className={state.danger ? 'danger' : ''}
                  onClick={() => close(true)}
                >
                  {state.confirmText}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

function useDialog(name) {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error(`${name} 必须在 ConfirmProvider 内使用`);
  }
  return context[name];
}

export function useConfirm() {
  return useDialog('confirm');
}

export function useChoose() {
  return useDialog('choose');
}
