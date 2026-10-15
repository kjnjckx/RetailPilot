import React, { useEffect, useRef } from "react";
import { FileText, X } from "lucide-react";
import { statusLabel } from "../lib";

export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const scroll = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = scroll;
      previous?.focus();
    };
  }, []);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const nodes = Array.from(
          dialog.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary",
          ) || [],
        ).filter((el) => el.getClientRects().length > 0);
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={"modal " + (wide ? "wide" : "")}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button
            autoFocus
            className="icon-btn"
            aria-label="关闭"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}

export function Badge({ status }: { status: string }) {
  return (
    <span className={"badge " + status}>
      <i />
      {statusLabel[status] || status}
    </span>
  );
}

export function Empty({
  text = "暂无记录",
  hint = "开始一次分析，让数据产生价值。",
  action,
}: {
  text?: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <FileText size={27} />
      </div>
      <h3>{text}</h3>
      <p>{hint}</p>
      {action}
    </div>
  );
}
