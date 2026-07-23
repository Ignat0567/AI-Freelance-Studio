import React, { useEffect, useRef } from 'react';

export default function SandboxTestLabConfirmDialog({ operation, pending, onConfirm, onClose }) {
  const dialogRef = useRef(null);
  const cancelRef = useRef(null);

  useEffect(() => {
    const previousFocus = document.activeElement;
    cancelRef.current?.focus();
    return () => previousFocus?.focus?.();
  }, []);

  useEffect(() => {
    if (pending) dialogRef.current?.focus();
  }, [pending]);

  const onKeyDown = event => {
    if (event.key === 'Escape' && !pending) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = [...dialogRef.current.querySelectorAll('button:not(:disabled)')];
    if (!controls.length) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="fs-test-lab-dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && !pending && onClose()}>
      <section
        ref={dialogRef}
        className="fs-test-lab-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="test-lab-confirm-title"
        aria-describedby="test-lab-confirm-description"
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <span className="fs-eyebrow">Confirm controlled execution</span>
        <h2 id="test-lab-confirm-title">Launch {operation.name}?</h2>
        <div id="test-lab-confirm-description" className="fs-test-lab-confirm-copy">
          <p>Windows Sandbox may open and this validation can take several minutes.</p>
          <p>Cancellation is cooperative and may wait for a safe interruption point.</p>
          <p>Studio controls only the session created for this run. Unrelated Sandbox sessions are not controlled.</p>
        </div>
        <div className="fs-test-lab-dialog-actions">
          <button ref={cancelRef} type="button" className="fs-secondary" disabled={pending} onClick={onClose}>Go back</button>
          <button type="button" className="fs-primary" disabled={pending} onClick={onConfirm}>
            {pending ? 'Launching...' : 'Launch controlled run'}
          </button>
        </div>
      </section>
    </div>
  );
}
