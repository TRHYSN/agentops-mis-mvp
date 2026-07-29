import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type ThemeMode = "enterprise" | "ops" | "workforce";
export type LocaleMode = "en" | "zh";

interface PreferencesContextValue {
  theme: ThemeMode;
  locale: LocaleMode;
  setTheme: (theme: ThemeMode) => void;
  setLocale: (locale: LocaleMode) => void;
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

const THEME_KEY = "agentops-mis-theme";
const LOCALE_KEY = "agentops-mis-locale";

function readTheme(): ThemeMode {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "light" || stored === "enterprise") return "enterprise";
  if (stored === "dark" || stored === "ops") return "ops";
  if (stored === "workforce") return "workforce";
  return "enterprise";
}

function readLocale(): LocaleMode {
  const stored = localStorage.getItem(LOCALE_KEY);
  if (stored === "en" || stored === "zh") return stored;
  return "zh";
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>(readTheme);
  const [locale, setLocaleState] = useState<LocaleMode>(readLocale);

  const setTheme = (nextTheme: ThemeMode) => {
    setThemeState(nextTheme);
    localStorage.setItem(THEME_KEY, nextTheme);
  };

  const setLocale = (nextLocale: LocaleMode) => {
    setLocaleState(nextLocale);
    localStorage.setItem(LOCALE_KEY, nextLocale);
  };

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme !== "enterprise");
    document.documentElement.dataset.agentopsTheme = theme;
  }, [theme]);

  const value = useMemo(
    () => ({ theme, locale, setTheme, setLocale }),
    [theme, locale],
  );

  return (
    <PreferencesContext.Provider value={value}>
      {children}
    </PreferencesContext.Provider>
  );
}

export function usePreferences() {
  const value = useContext(PreferencesContext);
  if (!value) {
    throw new Error("usePreferences must be used inside PreferencesProvider");
  }
  return value;
}

export function pick<T>(locale: LocaleMode, copy: { en: T; zh: T }): T {
  return locale === "zh" ? copy.zh : copy.en;
}
