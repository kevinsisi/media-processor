/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Replaced at build time by Vite's `define` in vite.config.ts.
declare const __APP_VERSION__: string;
