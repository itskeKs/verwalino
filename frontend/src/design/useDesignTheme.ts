import { createContext, useContext } from "react";
import type { DesignTheme } from "../api/types";

export interface DesignThemeState {
  theme: DesignTheme;
  loading: boolean;
  loaded: boolean;
  saving: boolean;
  error: string | null;
  reload: () => void;
  save: (theme: DesignTheme) => Promise<boolean>;
}

// Existing page tests and standalone components render with the default design.
export const DesignThemeCtx = createContext<DesignThemeState>({
  theme: "klar", loading: false, loaded: false, saving: false, error: null,
  reload: () => {}, save: async () => false,
});

export function useDesignTheme() { return useContext(DesignThemeCtx); }
