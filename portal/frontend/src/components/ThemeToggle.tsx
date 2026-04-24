import { useAppTheme } from "../lib/theme";

export default function ThemeToggle() {
  const { theme, toggleTheme } = useAppTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      role="switch"
      aria-checked={isDark}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={toggleTheme}
    >
      <span className={`theme-toggle-track ${isDark ? "on" : "off"}`}>
        <span className="theme-toggle-thumb">
          {isDark ? <MoonGlyph /> : <SunGlyph />}
        </span>
      </span>
      <span className="theme-toggle-label">{isDark ? "Dark" : "Light"}</span>
    </button>
  );
}

function SunGlyph() {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden>
      <circle cx="8" cy="8" r="3" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <line x1="8" y1="1.5" x2="8" y2="3" />
        <line x1="8" y1="13" x2="8" y2="14.5" />
        <line x1="1.5" y1="8" x2="3" y2="8" />
        <line x1="13" y1="8" x2="14.5" y2="8" />
        <line x1="3.3" y1="3.3" x2="4.4" y2="4.4" />
        <line x1="11.6" y1="11.6" x2="12.7" y2="12.7" />
        <line x1="3.3" y1="12.7" x2="4.4" y2="11.6" />
        <line x1="11.6" y1="4.4" x2="12.7" y2="3.3" />
      </g>
    </svg>
  );
}

function MoonGlyph() {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M13 10.5A5.5 5.5 0 0 1 5.5 3c0-.3 0-.6.1-.9A6 6 0 1 0 13.9 10.4c-.3 0-.6.1-.9.1Z"
        fill="currentColor"
      />
    </svg>
  );
}
