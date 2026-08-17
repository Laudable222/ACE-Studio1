import { useEffect, useState } from "react";

// Light/dark, remembered. The tokens support both; dark is the native look.
export function useTheme() {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("ace2-theme") || "dark");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("ace2-theme", theme);
  }, [theme]);
  return { theme, setTheme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}
