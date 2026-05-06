/* api.js — thin wrapper over the editor server endpoints.
 *
 * Every method returns a Promise resolving to {ok, status, body}, where
 * body is the parsed JSON response. Callers handle ok=false themselves so
 * the editor can render diagnostics inline rather than throwing.
 */

const BTApi = (() => {
  async function _call(method, path, body = null) {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
    const resp = await fetch(path, opts);
    let parsed = null;
    try {
      parsed = await resp.json();
    } catch {
      parsed = null;
    }
    return { ok: resp.ok, status: resp.status, body: parsed };
  }

  return {
    catalog:   () => _call("GET",  "/catalog"),
    listTrees: () => _call("GET",  "/trees"),
    getTree:   (name) => _call("GET",  `/trees/${encodeURIComponent(name)}`),
    putTree:   (name, doc) => _call("PUT", `/trees/${encodeURIComponent(name)}`, doc),
    publish:   (name, { updateSnapshot = false } = {}) => {
      const qs = updateSnapshot ? "?update_snapshot=true" : "";
      return _call("POST", `/trees/${encodeURIComponent(name)}/publish${qs}`);
    },
  };
})();
