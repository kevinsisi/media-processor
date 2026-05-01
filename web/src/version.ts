// Build-time-injected app version from web/package.json.
// See vite.config.ts (`define: { __APP_VERSION__ }`) and vite-env.d.ts.

export const APP_VERSION: string = __APP_VERSION__;
