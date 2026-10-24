import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/** Dark Telemetry (default) and Light Laboratory modes of the Deterministic Systems Workbench theme. */
export type Theme = "dark" | "light";
type ThemeValue = { theme: Theme; toggle: () => void };

const ThemeContext = createContext<ThemeValue | null>(null);

function stored(): Theme {
  try {
    return localStorage.getItem("ca-theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(stored);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem("ca-theme", theme);
    } catch {
      /* private mode */
    }
    window.dispatchEvent(new CustomEvent("ca-theme", { detail: theme }));
  }, [theme]);
  const value = useMemo<ThemeValue>(() => ({ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }), [theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("ThemeProvider is missing");
  return value;
}

/** Reads a theme colour token as a CSS colour string (for SVG and Mermaid). */
export function token(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return alpha === 1 ? `rgb(${raw})` : `rgb(${raw} / ${alpha})`;
}
