import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";

// Read version from VERSION file
function getVersion(): string {
  const versionPaths = [
    path.resolve(__dirname, "../VERSION"),
    path.resolve(__dirname, "VERSION"),
  ];

  for (const versionPath of versionPaths) {
    try {
      if (fs.existsSync(versionPath)) {
        return fs.readFileSync(versionPath, "utf-8").trim();
      }
    } catch {
      // Continue to next path
    }
  }

  return "0.0.0";
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(getVersion()),
  },
});
