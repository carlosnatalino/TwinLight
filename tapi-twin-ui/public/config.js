// Runtime configuration — read by the app as the default Digital Twin URL.
//
// In a container deployment this file is REGENERATED at startup from the
// TWIN_URL environment variable (see docker-entrypoint.d/40-twin-config.sh),
// so the same image can be pointed at any twin without a rebuild. During a
// local `npm run dev`/`npm run build` it just supplies the default the
// connection store falls back to. Point the UI elsewhere by setting the
// TWIN_URL env var, not by editing this file.
window.__TWIN_CONFIG__ = { baseUrl: "http://localhost:8080" };
