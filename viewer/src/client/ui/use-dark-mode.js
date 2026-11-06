import { useEffect, useState } from "react";

/** True while the app runs in dark mode (`applyColorSchemeToDocument` puts `.dark` on <html>). */
export function isDarkDocument(root = typeof document === "undefined" ? null : document.documentElement) {
  return Boolean(root?.classList?.contains("dark"));
}

export function useDarkMode() {
  const [dark, setDark] = useState(() => isDarkDocument());
  useEffect(() => {
    const root = typeof document === "undefined" ? null : document.documentElement;
    if (!root || typeof MutationObserver === "undefined") return undefined;
    setDark(isDarkDocument(root));
    const observer = new MutationObserver(() => setDark(isDarkDocument(root)));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}
