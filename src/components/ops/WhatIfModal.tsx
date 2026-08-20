import { useEffect, useRef } from "react";
import { useTwin } from "@/twin/store";
import { DecisionPanel } from "./DecisionPanel";
import { OptionsPanel, SafetyPanel } from "./OptionsPanel";
import { Btn } from "./primitives";

/**
 * Full-screen What-if workflow. The conflict code (#8) and the "ESC to close"
 * hint (#15) are gone; the header now says where the problem actually is.
 */
export function WhatIfModal() {
  const { whatIfOpen, selectedConflict, closeWhatIf } = useTwin();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!whatIfOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeWhatIf();
    };
    window.addEventListener("keydown", onKey);
    closeRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [whatIfOpen, closeWhatIf]);

  if (!whatIfOpen || !selectedConflict) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-3 sm:p-6"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closeWhatIf();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="what-if-title"
        className="flex max-h-[calc(100vh-1.5rem)] w-full max-w-6xl flex-col overflow-hidden border border-selected/60 bg-panel shadow-2xl sm:max-h-[calc(100vh-3rem)]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-border bg-shell px-3 py-2">
          <span className="h-4 w-px bg-selected" aria-hidden />
          <div>
            <h2
              id="what-if-title"
              className="font-cond text-[13px] tracking-[0.12em] text-selected uppercase"
            >
              {selectedConflict.resourceLabel}
            </h2>
            <p className="text-[10.5px] text-faint">
              {selectedConflict.severity === "CRITICAL" ? "Critical" : "Warning"}
              {" · "}
              {selectedConflict.trainB
                ? `${short(selectedConflict.trainA)} and ${short(selectedConflict.trainB)}`
                : short(selectedConflict.trainA)}
            </p>
          </div>
          <div className="ml-auto">
            <Btn ref={closeRef} variant="quiet" onClick={closeWhatIf}>
              Close
            </Btn>
          </div>
        </header>

        <div className="min-h-0 overflow-y-auto p-px">
          <div className="grid min-h-0 gap-px bg-border lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
            <DecisionPanel className="min-h-[420px]" />
            <div className="grid min-h-0 gap-px bg-border lg:grid-rows-[minmax(220px,1fr)_minmax(170px,0.7fr)]">
              <OptionsPanel className="min-h-[220px]" />
              <SafetyPanel className="min-h-[170px]" />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

/** Ids look like L-90632-308; a controller only ever says "90632". */
function short(id: string): string {
  const parts = id.split("-");
  return parts.length > 1 ? (parts[1] as string) : id;
}
