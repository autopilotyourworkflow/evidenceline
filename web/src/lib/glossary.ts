// Shared open/closed state for the tap-to-explain terms.
// Hover shows an explanation; a click or tap pins it open until you click elsewhere, click it again or press Escape.
// Only one term can be pinned at a time; opening one closes the others.

import { useSyncExternalStore } from 'react';

type State = { readonly pinned: string | null; readonly hovered: ReadonlySet<string> };

let state: State = { pinned: null, hovered: new Set() };
const listeners = new Set<() => void>();

function set(next: State): void {
  state = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function closeAll(): void {
  if (state.pinned === null && state.hovered.size === 0) return;
  set({ pinned: null, hovered: new Set() });
}

/** A click toggles the pin: pinning one term closes every other. */
export function togglePin(id: string): void {
  const wasPinned = state.pinned === id;
  set({ pinned: wasPinned ? null : id, hovered: new Set() });
}

export function hover(id: string, on: boolean): void {
  const hovered = new Set(state.hovered);
  if (on) hovered.add(id);
  else hovered.delete(id);
  set({ ...state, hovered });
}

export function useTermOpen(id: string): { open: boolean; pinned: boolean } {
  const current = useSyncExternalStore(subscribe, () => state, () => state);
  return { open: current.pinned === id || current.hovered.has(id), pinned: current.pinned === id };
}

let installed = false;

/** Document-level listeners: a click anywhere else or Escape closes every explanation. Safe to call more than once. */
export function installGlossaryListeners(): () => void {
  if (installed) return () => undefined;
  installed = true;
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') closeAll();
  };
  document.addEventListener('click', closeAll);
  document.addEventListener('keydown', onKey);
  return () => {
    installed = false;
    document.removeEventListener('click', closeAll);
    document.removeEventListener('keydown', onKey);
  };
}
