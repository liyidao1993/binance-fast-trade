/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0b0e14",
          soft: "#111827",
          raised: "#1f2937",
        },
        border: "#1f2937",
        text: {
          DEFAULT: "#e5e7eb",
          muted: "#9ca3af",
          soft: "#d1d5db",
        },
        accent: {
          up: "#22c55e",
          down: "#ef4444",
          warn: "#f59e0b",
          info: "#3b82f6",
        },
      },
      fontFamily: {
        mono: [
          "JetBrains Mono",
          "SF Mono",
          "ui-monospace",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
