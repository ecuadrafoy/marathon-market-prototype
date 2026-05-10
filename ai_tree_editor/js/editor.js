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
  const $btnNewTree = document.getElementById("btn-new-tree");
  const $btnNewLeaf = document.getElementById("btn-new-leaf");
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

  // ----- New Tree modal --------------------------------------------------
  // Cached scaffold response and tree-list snapshot, used to disable "Create"
  // when the chosen <kind>_<doctrine> already exists.
  let cachedScaffolds = null;
  let cachedTreeNames = new Set();

  async function refreshNewTreeData() {
    const [scaffoldsResp, treesResp] = await Promise.all([
      BTApi.scaffolds(),
      BTApi.listTrees(),
    ]);
    cachedScaffolds = scaffoldsResp.ok ? scaffoldsResp.body : { kinds: [], doctrines: [] };
    if (treesResp.ok) {
      cachedTreeNames = new Set([
        ...treesResp.body.drafts,
        ...treesResp.body.published,
      ]);
    }
  }

  function _populateKindSelect() {
    const $kind = document.getElementById("nt-kind");
    $kind.innerHTML = "";
    for (const k of cachedScaffolds.kinds) {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      $kind.appendChild(opt);
    }
  }

  function _populateDoctrineSelect() {
    const $sel = document.getElementById("nt-doctrine");
    $sel.innerHTML = '<option value="">— select —</option>';
    for (const d of cachedScaffolds.doctrines) {
      const opt = document.createElement("option");
      opt.value = d;
      opt.textContent = d;
      $sel.appendChild(opt);
    }
  }

  function _ntComputeName() {
    const kind = document.getElementById("nt-kind").value;
    const doctrine = document.getElementById("nt-doctrine").value;
    return kind && doctrine ? `${kind}_${doctrine}` : "";
  }

  function _ntUpdatePreview() {
    const $preview = document.getElementById("nt-preview");
    const $msg = document.getElementById("nt-preview-msg");
    const $submit = document.getElementById("nt-submit");

    const name = _ntComputeName();
    $preview.textContent = name || "—";

    const conflict = name && cachedTreeNames.has(name);
    $submit.disabled = !name || conflict;

    if (conflict) {
      $msg.textContent = `${name} already exists — load it instead.`;
      $msg.className = "preview-msg preview-msg-error";
      $msg.hidden = false;
    } else {
      $msg.hidden = true;
    }
  }

  $btnNewTree.addEventListener("click", async () => {
    await refreshNewTreeData();
    _populateKindSelect();
    _populateDoctrineSelect();
    _ntUpdatePreview();
    BTModals.openModal("new-tree-modal");
  });

  document.getElementById("nt-kind").addEventListener("change", _ntUpdatePreview);
  document.getElementById("nt-doctrine").addEventListener("change", _ntUpdatePreview);

  document.getElementById("new-tree-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = _ntComputeName();
    if (!name) return;
    setStatus(`Creating ${name}…`);
    const r = await BTApi.createTree(name);
    if (!r.ok) {
      BTModals.renderFieldErrors(
        document.getElementById("new-tree-modal"),
        [{ field: "doctrine", message: (r.body && r.body.error) || `HTTP ${r.status}` }],
      );
      setStatus(`Create failed (HTTP ${r.status})`, "error");
      return;
    }
    BTModals.closeModal("new-tree-modal");
    await refreshTreeList();
    $tree.value = name;
    $tree.dispatchEvent(new Event("change"));
    // Auto-load to drop the empty Selector onto the canvas
    $btnLoad.click();
    setStatus(`Created ${name}`, "success");
  });

  // ----- New Leaf modal --------------------------------------------------
  function _nlExistingLeafNames() {
    return new Set((currentCatalog?.leaves || []).map((l) => l.name));
  }

  function _nlExistingCategories() {
    return [...new Set((currentCatalog?.leaves || []).map((l) => l.category))].sort();
  }

  function _nlExistingRequires() {
    const all = new Set();
    for (const l of currentCatalog?.leaves || []) {
      for (const r of l.requires || []) all.add(r);
    }
    return [...all].sort();
  }

  function _nlPopulateSuggestions() {
    const $catList = document.getElementById("nl-category-list");
    $catList.innerHTML = "";
    for (const c of _nlExistingCategories()) {
      const opt = document.createElement("option");
      opt.value = c;
      $catList.appendChild(opt);
    }
    const $reqList = document.getElementById("nl-requires-list");
    $reqList.innerHTML = "";
    for (const r of _nlExistingRequires()) {
      const opt = document.createElement("option");
      opt.value = r;
      $reqList.appendChild(opt);
    }
  }

  // ----- Requires chip input -----
  let nlRequiresChips = [];
  function _nlRenderRequires() {
    const $box = document.getElementById("nl-requires-chips");
    // Remove all chip elements but keep the input + datalist
    for (const chip of $box.querySelectorAll(".chip")) chip.remove();
    const $input = document.getElementById("nl-requires-input");
    for (const name of nlRequiresChips) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${BTModals.escapeHtml(name)} <button type="button" aria-label="Remove">×</button>`;
      chip.querySelector("button").addEventListener("click", () => {
        nlRequiresChips = nlRequiresChips.filter((x) => x !== name);
        _nlRenderRequires();
        _nlUpdatePreview();
      });
      $box.insertBefore(chip, $input);
    }
  }

  document.getElementById("nl-requires-input").addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== ",") return;
    ev.preventDefault();
    const v = ev.target.value.trim();
    if (v && /^[a-z_][a-z0-9_]*$/.test(v) && !nlRequiresChips.includes(v)) {
      nlRequiresChips.push(v);
      _nlRenderRequires();
      _nlUpdatePreview();
    }
    ev.target.value = "";
  });

  // ----- Param rows -----
  function _nlAddParamRow(seed = {}) {
    const $tbody = document.querySelector("#nl-params-table tbody");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input class="p-name" pattern="[a-z_][a-z0-9_]*" placeholder="threshold" value="${BTModals.escapeHtml(seed.name || "")}"></td>
      <td>
        <select class="p-type">
          <option value="float">float</option>
          <option value="int">int</option>
          <option value="bool">bool</option>
          <option value="str">str</option>
        </select>
      </td>
      <td><input class="p-default" placeholder="0.75" value="${BTModals.escapeHtml(seed.default ?? "")}"></td>
      <td><input class="p-desc" placeholder="cutoff value" value="${BTModals.escapeHtml(seed.description || "")}"></td>
      <td><button type="button" class="btn-small btn-secondary remove-param">×</button></td>
    `;
    tr.querySelector(".p-type").value = seed.type || "float";
    tr.querySelector(".remove-param").addEventListener("click", () => {
      tr.remove();
      _nlUpdatePreview();
    });
    for (const inp of tr.querySelectorAll("input, select")) {
      inp.addEventListener("input", _nlUpdatePreview);
    }
    $tbody.appendChild(tr);
    _nlUpdatePreview();
  }

  document.getElementById("nl-add-param").addEventListener("click", () => _nlAddParamRow());

  function _nlGatherParams() {
    const params = [];
    for (const tr of document.querySelectorAll("#nl-params-table tbody tr")) {
      const name = tr.querySelector(".p-name").value.trim();
      const type = tr.querySelector(".p-type").value;
      const defStr = tr.querySelector(".p-default").value;
      const desc = tr.querySelector(".p-desc").value;
      if (!name) continue;
      let def;
      if (defStr === "") {
        def = null;
      } else if (type === "int") {
        def = parseInt(defStr, 10);
        if (Number.isNaN(def)) def = defStr;
      } else if (type === "float") {
        def = parseFloat(defStr);
        if (Number.isNaN(def)) def = defStr;
      } else if (type === "bool") {
        def = /^(true|1|yes)$/i.test(defStr);
      } else {
        def = defStr;
      }
      params.push({ name, type, default: def, description: desc });
    }
    return params;
  }

  function _nlGatherSpec() {
    return {
      name: document.getElementById("nl-name").value.trim(),
      category: document.getElementById("nl-category").value.trim(),
      description: document.getElementById("nl-description").value.trim(),
      requires: [...nlRequiresChips],
      params: _nlGatherParams(),
      body: document.getElementById("nl-body").value,
    };
  }

  // ----- Live preview rendering (informational; server is authoritative) -----
  function _nlSnakeCase(name) {
    return name
      .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
      .toLowerCase();
  }

  function _nlReprPython(value) {
    if (typeof value === "string") {
      return "'" + value.replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
    }
    if (typeof value === "boolean") return value ? "True" : "False";
    if (value === null || value === undefined) return "None";
    return String(value);
  }

  function _nlRenderPreview(spec) {
    const funcName = _nlSnakeCase(spec.name || "leaf_name");
    const requiresList = "[" + spec.requires.map((r) => _nlReprPython(r)).join(", ") + "]";
    const paramSrc = spec.params
      .map((p) => `ParamSpec(name=${_nlReprPython(p.name)}, type=${p.type}, ` +
                  `default=${_nlReprPython(p.default)}, ` +
                  `description=${_nlReprPython(p.description)})`)
      .join(", ");
    const body = (spec.body || "    pass")
      .split("\n")
      .map((l) => "    " + l)
      .join("\n");
    return `from ai_tree.registry import bt_condition, ParamSpec\n\n\n` +
           `@bt_condition(\n` +
           `    name=${_nlReprPython(spec.name)},\n` +
           `    category=${_nlReprPython(spec.category)},\n` +
           `    description=${_nlReprPython(spec.description)},\n` +
           `    requires=${requiresList},\n` +
           `    params=[${paramSrc}],\n` +
           `)\n` +
           `def ${funcName}(ctx) -> bool:\n${body}\n`;
  }

  function _nlUpdatePreview() {
    const $preview = document.getElementById("nl-preview");
    if (!$preview) return;
    $preview.textContent = _nlRenderPreview(_nlGatherSpec());
  }

  // Trigger preview updates as the user types in any field
  for (const id of ["nl-name", "nl-category", "nl-description", "nl-body"]) {
    document.getElementById(id).addEventListener("input", _nlUpdatePreview);
  }

  $btnNewLeaf.addEventListener("click", () => {
    if (!currentCatalog) {
      setStatus("Catalog not loaded yet — wait a moment", "error");
      return;
    }
    // Reset the form
    document.getElementById("new-leaf-form").reset();
    document.querySelector("#nl-params-table tbody").innerHTML = "";
    nlRequiresChips = [];
    _nlRenderRequires();
    _nlPopulateSuggestions();
    _nlUpdatePreview();
    BTModals.openModal("new-leaf-modal");
  });

  document.getElementById("new-leaf-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const spec = _nlGatherSpec();

    // Cheap client-side sanity check before bothering the server
    const $modal = document.getElementById("new-leaf-modal");
    BTModals.clearFieldErrors($modal);
    if (_nlExistingLeafNames().has(spec.name)) {
      BTModals.renderFieldErrors($modal, [
        { field: "name", message: `'${spec.name}' is already in the catalog` },
      ]);
      return;
    }

    setStatus(`Creating leaf ${spec.name}…`);
    const r = await BTApi.createLeaf(spec);
    if (!r.ok) {
      BTModals.renderFieldErrors($modal, (r.body && r.body.errors) || []);
      setStatus(`Create leaf failed (HTTP ${r.status})`, "error");
      return;
    }
    BTModals.closeModal("new-leaf-modal");
    await loadCatalog();
    setStatus(`Created leaf ${spec.name}`, "success");
  });

  // Cancel buttons (any element with data-close-modal)
  for (const btn of document.querySelectorAll("[data-close-modal]")) {
    btn.addEventListener("click", () => {
      BTModals.closeModal(btn.dataset.closeModal);
    });
  }

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
