import { useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "./ConfirmDialog.css";

export interface ConfirmDialogOptions {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
}

interface ConfirmRequest extends ConfirmDialogOptions {
  resolve: (confirmed: boolean) => void;
}

export type ConfirmFn = (options: ConfirmDialogOptions) => Promise<boolean>;

export function useConfirmDialog(): {
  confirm: ConfirmFn;
  confirmDialog: ReactNode;
} {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setRequest({ ...options, resolve });
    });
  }, []);

  const close = useCallback(
    (confirmed: boolean) => {
      if (!request) return;
      request.resolve(confirmed);
      setRequest(null);
    },
    [request],
  );

  const confirmDialog = request
    ? createPortal(
        <div className="confirm-dialog__backdrop" role="presentation">
          <section
            className={`confirm-dialog confirm-dialog--${request.tone ?? "default"}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
          >
            <div className="confirm-dialog__body">
              <h2 id="confirm-dialog-title" className="confirm-dialog__title">
                {request.title}
              </h2>
              <div className="confirm-dialog__message">{request.message}</div>
            </div>
            <div className="confirm-dialog__actions">
              <button
                type="button"
                className="confirm-dialog__button confirm-dialog__button--cancel"
                onClick={() => close(false)}
              >
                {request.cancelLabel ?? "取消"}
              </button>
              <button
                type="button"
                className="confirm-dialog__button confirm-dialog__button--confirm"
                onClick={() => close(true)}
              >
                {request.confirmLabel ?? "確認"}
              </button>
            </div>
          </section>
        </div>,
        document.body,
      )
    : null;

  return { confirm, confirmDialog };
}
