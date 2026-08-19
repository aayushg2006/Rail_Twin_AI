import { useEffect, useRef } from "react";
import { useTwin } from "@/twin/store";
import { DecisionPanel } from "./DecisionPanel";
import { OptionsPanel, SafetyPanel } from "./OptionsPanel";
import { Btn } from "./primitives";

/** Full-screen What-if workflow opened directly from a map/list conflict. */
export function WhatIfModal() {
  const { whatIfOpen, selectedConflict, closeWhatIf } = useTwin();
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!whatIfOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeWhatIf();
    };
    window.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [whatIfOpen, closeWhatIf]);

  if (!whatIfOpen || !selectedConflict) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 sm:p-6"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeWhatIf();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="what-if-title"
        className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-6xl flex-col overflow-hidden border border-selected/60 bg-panel shadow-2xl sm:max-h-[calc(100vh-3rem)]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-border bg-shell px-3 py-2">
          <span className="h-4 w-px bg-selected" aria-hidden />
          <div>
            <h2 id="what-if-title" className="font-cond text-[13px] tracking-[0.12em] text-selected uppercase">
              What-if decision
            </h2>
            <p className="num text-[10px] text-faint">
              {selectedConflict.id} · {selectedConflict.resourceLabel}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-[10px] text-faint sm:inline">ESC to close</span>
            <Btn ref={closeButtonRef} variant="quiet" onClick={closeWhatIf}>
              Close
            </Btn>
          </div>
        </header>

        <div className="min-h-0 overflow-y-auto p-px">
          <div className="grid min-h-0 gap-px bg-border lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
            <DecisionPanel className="min-h-[420px]" />
            <div className="grid min-h-0 gap-px bg-border lg:grid-rows-[minmax(220px,1fr)_minmax(180px,0.8fr)]">
              <OptionsPanel className="min-h-[220px]" />
              <SafetyPanel className="min-h-[180px]" />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
