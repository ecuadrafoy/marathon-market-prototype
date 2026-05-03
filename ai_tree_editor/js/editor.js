/* editor.js — top-level wiring for the behaviour-tree editor.
 *
 * Boot order:
 *   1. Register built-in composite/decorator node types in LiteGraph.
 *   2. Fetch /catalog → register a node type for every leaf.
 *   3. Fetch /trees → populate the tree-name dropdown.
 *   4. Hook up Load / Save / Publish / Refresh palette buttons.
 *
 * Manual refresh policy: the palette only re-fetches /catalog when the user
 * clicks "Refresh palette". This matches the design choice that you don't
 * change leaves often, and explicit beats invisible auto-reloads.
 */

(function () {
  // ----- DOM refs --------------------------------------------------------
  const $tree = document.getElementById("tree-name");
  const $btnLoad = document.getElementById("btn-load");
  const $btnSave = document.getElementById("btn-save");
  const $btnPublish = document.getElementById("btn-publish");
  const $btnRefreshPalette = document.getElementById("btn-refresh-palette");
  const $palette = document.getElementById("palette");
  const $diag = document.getElementById("diagnostics");
  const $status = document.getElementById("status");

  // ----- Graph + canvas --------------------------------------------------
  BTNodes.registerBuiltins();
  const graph = new LGraph();
  const canvas = new LGraphCanvas("#bt-canvas", graph);
  graph.start();   // not strictly needed (no tick loop) but harmless

  // ----- State -----------------------------------------------------------
  let currentCatalog = null;

  // ----- Helpers ---------------------------------------------------------
  function setStatus(msg, kind = "") {
    $status.textContent = msg;
    $status.className = "status " + (kind || "");
  }

  function renderPalette(catalog) {
    $palette.innerHTML = "<h2>Palette</h2>";

    // Composites first, then leaves grouped by category.
    const composites = document.createElement("div");
    composites.className = "palette-category palette-section-composites";
    composites.innerHTML = "<h3>Composites</h3>";
    for (const c of [
      { type: "bt/composite/Sequence", label: "Sequence",
        desc: "AND across children. Fails on first child failure." },
      { type: "bt/composite/Selector", label: "Selector",
        desc: "OR across children. Succeeds on first child success." },
      { type: "bt/decorator/Inverter", label: "Inverter",
        desc: "Flips success and failure of its single child." },
    ]) {
      composites.appendChild(_paletteItem(c.type, c.label, c.desc));
    }
    $palette.appendChild(composites);

    // Group leaves by category.
    const grouped = {};
    for (const spec of catalog.leaves) {
      (grouped[spec.category] = grouped[spec.category] || []).push(spec);
    }
    for (const [cat, specs] of Object.entries(grouped).sort()) {
      const sec = document.createElement("div");
      sec.className = "palette-category";
      sec.innerHTML = `<h3>${_escape(cat)}</h3>`;
      for (const spec of specs) {
        sec.appendChild(_paletteItem(
          `bt/leaf/${spec.name}`,
          spec.name,
          spec.description,
        ));
      }
      $palette.appendChild(sec);
    }
  }

  function _paletteItem(liteGraphType, label, desc) {
    const div = document.createElement("div");
    div.className = "palette-item";
    div.innerHTML = `<strong>${_escape(label)}</strong>` +
                    (desc ? `<span class="desc">${_escape(desc)}</span>` : "");
    div.title = "Double-click on the canvas, then choose this node from the menu.";
    div.addEventListener("dblclick", () => {
      const node = LiteGraph.createNode(liteGraphType);
      if (!node) {
        setStatus(`Could not instantiate ${liteGraphType}`, "error");
        return;
      }
      // Drop near the canvas centre.
      const cv = canvas.canvas;
      node.pos = [
        canvas.visible_area[0] + cv.width / 4,
        canvas.visible_area[1] + cv.height / 4,
      ];
      graph.add(node);
    });
    return div;
  }

  function renderDiagnostics(diags, ok) {
    $diag.innerHTML = "";
    $diag.classList.remove("empty");
    if (!diags || diags.length === 0) {
      const div = document.createElement("div");
      div.className = "diag " + (ok ? "success" : "error");
      div.textContent = ok ? "Publish succeeded with no diagnostics."
                           : "Publish failed but no diagnostics returned.";
      $diag.appendChild(div);
      return;
    }
    for (const d of diags) {
      const div = document.createElement("div");
      div.className = "diag " + (d.severity === "error" ? "error" : "warning");
      div.innerHTML = `<span class="check">${_escape(d.check)}</span>` +
                      `${_escape(d.message)}`;
      $diag.appendChild(div);
    }
  }

  function setDiagnosticsEmpty() {
    $diag.className = "diagnostics empty";
    $diag.textContent = "No publish run yet.";
  }

  function _escape(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  // ----- Catalog loading -------------------------------------------------
  async function loadCatalog() {
    setStatus("Loading catalog…");
    const r = await BTApi.catalog();
    if (!r.ok) {
      setStatus(`Catalog load failed (HTTP ${r.status})`, "error");
      return;
    }
    currentCatalog = r.body;
    BTNodes.registerLeaves(currentCatalog);
    renderPalette(currentCatalog);
    setStatus(`Catalog loaded — ${currentCatalog.leaves.length} leaves`);
  }

  // ----- Tree list -------------------------------------------------------
  async function refreshTreeList() {
    const r = await BTApi.listTrees();
    if (!r.ok) {
      setStatus(`Tree list failed (HTTP ${r.status})`, "error");
      return;
    }
    const all = new Set([...r.body.drafts, ...r.body.published]);
    $tree.innerHTML = '<option value="">— select —</option>';
    for (const name of [...all].sort()) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name +
        (r.body.published.includes(name) ? " (published)" : " (draft)");
      $tree.appendChild(opt);
    }
  }

  $tree.addEventListener("change", () => {
    const ready = !!$tree.value;
    $btnLoad.disabled = !ready;
    $btnSave.disabled = !ready;
    $btnPublish.disabled = !ready;
  });

  // ----- Load tree -------------------------------------------------------
  $btnLoad.addEventListener("click", async () => {
    const name = $tree.value;
    if (!name) return;
    setStatus(`Loading ${name}…`);
    const r = await BTApi.getTree(name);
    if (!r.ok) {
      setStatus(`Load failed (HTTP ${r.status}): ${r.body && r.body.error}`,
                "error");
      return;
    }
    try {
      BTSerialize.treeToGraph(graph, r.body);
      setStatus(`Loaded ${name}`, "success");
      setDiagnosticsEmpty();
    } catch (e) {
      setStatus(`Tree could not be reconstructed: ${e.message}`, "error");
    }
  });

  // ----- Save draft ------------------------------------------------------
  $btnSave.addEventListener("click", async () => {
    const name = $tree.value;
    if (!name) return;
    let doc;
    try {
      doc = BTSerialize.graphToTree(graph, name);
    } catch (e) {
      setStatus(`Cannot save: ${e.message}`, "error");
      return;
    }
    setStatus(`Saving ${name}…`);
    const r = await BTApi.putTree(name, doc);
    if (!r.ok) {
      setStatus(`Save failed (HTTP ${r.status})`, "error");
      return;
    }
    setStatus(`Saved draft ${name}`, "success");
  });

  // ----- Publish ---------------------------------------------------------
  $btnPublish.addEventListener("click", async () => {
    const name = $tree.value;
    if (!name) return;
    // Save first so the publish gate sees the latest graph.
    let doc;
    try {
      doc = BTSerialize.graphToTree(graph, name);
    } catch (e) {
      setStatus(`Cannot publish: ${e.message}`, "error");
      return;
    }
    const saveR = await BTApi.putTree(name, doc);
    if (!saveR.ok) {
      setStatus(`Save before publish failed (HTTP ${saveR.status})`, "error");
      return;
    }
    setStatus(`Publishing ${name}…`);
    const pubR = await BTApi.publish(name);
    renderDiagnostics(pubR.body && pubR.body.diagnostics, pubR.ok);
    if (pubR.ok && pubR.body && pubR.body.success) {
      setStatus(`Published ${name} (${pubR.body.grid_size} grid inputs)`, "success");
      refreshTreeList();
    } else {
      setStatus(`Publish failed for ${name} — see diagnostics`, "error");
    }
  });

  // ----- Refresh palette -------------------------------------------------
  $btnRefreshPalette.addEventListener("click", loadCatalog);

  // ----- Boot ------------------------------------------------------------
  (async () => {
    await loadCatalog();
    await refreshTreeList();
  })();

  // Resize the canvas to fit its wrapper. LiteGraph doesn't auto-resize.
  function fitCanvas() {
    const wrap = document.querySelector(".canvas-wrapper");
    const cv = canvas.canvas;
    cv.width = wrap.clientWidth;
    cv.height = wrap.clientHeight;
    canvas.draw(true, true);
  }
  window.addEventListener("resize", fitCanvas);
  fitCanvas();
})();
