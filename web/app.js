const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  nodes: [],
  assets: [],
  pending: [],
  samples: [],
  metrics: {},
  selectedAssetId: null,
  selectedNodeId: null,
  suggestion: null,
  chosenRelation: null,
  suggestionShownAt: 0,
  expanded: new Set(),
  graph: null,
  currentView: "workspace",
  toastTimer: null,
};

const relationshipLabels = {
  "挂载": "挂载",
  "扩展": "扩展",
  "新建": "新建",
  "待定": "待定",
};

const statusLabels = {
  processing: "正在识别",
  classified: "已归入 · 可改",
  needs_review: "已建议 · 待确认",
  pending: "在未归类箱",
  manual_required: "待人工确认",
  recognition_failed: "识别失败 · 保留原图",
};

const actionLabels = {
  asset_decision: "调整素材位置",
  batch_asset_decision: "批量确认",
  node_move: "改挂节点",
  node_edit: "编辑节点",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function truncate(value, length = 42) {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function formatDate(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPercent(value) {
  const number = Number(value || 0);
  return `${Math.round(number * 100)}%`;
}

async function api(path, options = {}) {
  const config = { ...options };
  if (config.body && !(config.body instanceof FormData)) {
    config.headers = {
      "Content-Type": "application/json",
      ...(config.headers || {}),
    };
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      detail = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item.msg || String(item)).join("；")
        : payload.detail || detail;
    } catch {
      // 保留通用错误信息。
    }
    throw new Error(detail);
  }
  return response.json();
}

function showToast(message, tone = "normal") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${tone === "error" ? " error" : ""}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    toast.className = "toast";
  }, 3200);
}

function showLoading(message = "正在把这张板书接进知识网…") {
  $("#loadingText").textContent = message;
  $("#loadingOverlay").hidden = false;
  $(".paper-card")?.classList.add("scanning");
}

function hideLoading() {
  $("#loadingOverlay").hidden = true;
  $(".paper-card")?.classList.remove("scanning");
}

function applyBootstrap(data) {
  state.nodes = data.nodes || [];
  state.assets = data.assets || [];
  state.pending = data.pending || [];
  state.samples = data.samples || [];
  state.metrics = data.metrics || {};

  if (!state.expanded.size) {
    state.nodes
      .filter((node) => node.node_type === "course" || node.node_type === "chapter")
      .forEach((node) => state.expanded.add(node.id));
  }
}

async function loadBootstrap() {
  const data = await api("/api/bootstrap");
  applyBootstrap(data);
  renderSamples();
  renderTree();
  renderMetrics();
  renderPending();
  if (!state.suggestion) renderContext();
}

function setView(view) {
  state.currentView = view;
  $$(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
  $("#welcomeView").hidden = true;
  $("#reviewView").hidden = true;
  $("#graphView").hidden = true;
  $("#searchView").hidden = true;
  $("#revisionsView").hidden = true;

  if (view === "graph") {
    $("#graphView").hidden = false;
    loadGraph();
  } else if (view === "revisions") {
    $("#revisionsView").hidden = false;
    loadRevisions();
  } else if (view === "search") {
    $("#searchView").hidden = false;
  } else if (state.selectedAssetId) {
    $("#reviewView").hidden = false;
  } else {
    $("#welcomeView").hidden = false;
  }
}

function renderSamples() {
  const grid = $("#sampleGrid");
  grid.innerHTML = state.samples
    .map(
      (sample) => `
        <button class="sample-card" type="button" data-sample-id="${escapeHtml(sample.id)}">
          <img src="${escapeHtml(sample.original_url)}" alt="">
          <span class="sample-eyebrow">${escapeHtml(sample.eyebrow)}</span>
          <h3>${escapeHtml(sample.title)}</h3>
          <p>${escapeHtml(sample.summary)}</p>
        </button>
      `,
    )
    .join("");
}

function nodesByParent() {
  const map = new Map();
  for (const node of state.nodes) {
    const key = node.parent_id || "__root__";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(node);
  }
  for (const items of map.values()) {
    items.sort((a, b) => a.sort_order - b.sort_order || a.title.localeCompare(b.title, "zh-CN"));
  }
  return map;
}

function renderTree() {
  const tree = $("#tree");
  const skeleton = $("#treeSkeleton");
  skeleton.hidden = true;
  const children = nodesByParent();
  const roots = children.get("__root__") || [];
  const activeNodeId =
    state.selectedNodeId ||
    state.assets.find((asset) => asset.id === state.selectedAssetId)?.node_id;

  function branch(nodes, depth) {
    return nodes
      .map((node) => {
        const hasChildren = (children.get(node.id) || []).length > 0;
        const isOpen = state.expanded.has(node.id);
        const isActive = activeNodeId === node.id;
        return `
          <div class="tree-node-wrap">
            <button
              class="tree-node${isActive ? " active" : ""}"
              type="button"
              data-node-id="${escapeHtml(node.id)}"
              data-type="${escapeHtml(node.node_type)}"
              data-depth="${depth}"
              style="--depth:${depth}"
              draggable="${node.node_type !== "course"}"
            >
              <span class="tree-toggle${isOpen ? " open" : ""}" data-toggle-id="${escapeHtml(node.id)}" aria-hidden="true">
                ${hasChildren ? "▶" : ""}
              </span>
              <span class="tree-glyph" aria-hidden="true"></span>
              <span class="tree-label">${escapeHtml(node.title)}</span>
              ${node.asset_count ? `<span class="asset-count">${node.asset_count}</span>` : ""}
            </button>
            ${hasChildren && isOpen ? branch(children.get(node.id), depth + 1) : ""}
          </div>
        `;
      })
      .join("");
  }

  tree.innerHTML = branch(roots, 0);
  $("#nodeCount").textContent = `${state.nodes.length} 节点`;
}

function findAsset(assetId) {
  return state.assets.find((asset) => asset.id === assetId) || null;
}

function nodeTitle(nodeId) {
  return state.nodes.find((node) => node.id === nodeId)?.title || "根节点";
}

function statusTone(status) {
  if (status === "classified") return "good";
  if (status === "needs_review" || status === "manual_required") return "warn";
  if (status === "pending") return "pending";
  return "neutral";
}

function selectAsset(assetId) {
  const asset = findAsset(assetId);
  if (!asset) {
    showToast("没有找到这张素材", "error");
    return;
  }
  state.selectedAssetId = asset.id;
  state.selectedNodeId = asset.node_id;
  state.chosenRelation = null;
  state.suggestion = asset.suggested_relation
    ? {
        relation: asset.suggested_relation,
        targetId: asset.suggested_target_id,
        finalTargetId: asset.final_target_id,
        confidence: asset.confidence,
        reason: asset.reason,
        thresholdBand: asset.threshold_band,
        candidates: [],
      }
    : null;
  state.suggestionShownAt = Date.now();
  renderReview();
  renderTree();
  renderContext();
  setView("workspace");
}

function renderReview() {
  const asset = findAsset(state.selectedAssetId);
  if (!asset) return;
  $("#welcomeView").hidden = true;
  $("#reviewView").hidden = false;
  $("#reviewHeading").textContent = asset.title || "新的一张板书";
  $("#assetBreadcrumb").textContent = asset.node_title
    ? `高等数学 / ${asset.path || asset.node_title}`
    : "课堂板书 / 未归类箱";
  const status = $("#assetStatusPill");
  status.textContent = statusLabels[asset.status] || asset.status;
  status.dataset.tone = statusTone(asset.status);
  $("#originalFilename").textContent = asset.original_filename;
  $("#originalImage").src = asset.original_url || "";
  $("#originalImage").alt = `${asset.title}原图`;
  $("#assetTitleInput").value = asset.title || "";
  $("#summaryInput").value = asset.summary || "";
  $("#textInput").value = asset.recognized_text || "";
  $("#latexInput").value = asset.latex || "";
  $("#manualNotice").hidden = asset.status !== "manual_required";
  $("#regionStrip").innerHTML = (asset.regions || [])
    .map(
      (region) => `
        <span class="region-chip">
          <strong>${escapeHtml(region.type || "region")}</strong>
          ${escapeHtml(truncate(region.text || region.latex || "保留区域", 28))}
          <small>${formatPercent(region.confidence)}</small>
        </span>
      `,
    )
    .join("");
}

function compactNodeLabel(node) {
  return node.title;
}

function renderContext() {
  const asset = findAsset(state.selectedAssetId);
  const suggestion = state.suggestion;
  $("#contextEmpty").hidden = Boolean(asset && suggestion);
  $("#suggestionPanel").hidden = !(asset && suggestion);
  $("#pendingPanel").hidden = Boolean(asset) || state.pending.length === 0;

  if (!asset) {
    renderPending();
    return;
  }
  if (!suggestion) return;

  const confidence = Number(suggestion.confidence || 0);
  $("#thresholdLabel").textContent = {
    auto: "≥ 0.80 · 自动执行",
    review: "0.55–0.80 · 待确认",
    pending: "< 0.55 · 未归类箱",
  }[suggestion.thresholdBand] || "结构建议";
  $("#relationValue").textContent = relationshipLabels[suggestion.relation] || suggestion.relation;
  $("#confidenceValue").textContent = formatPercent(confidence);
  $("#confidenceRing").style.setProperty("--confidence", `${Math.max(0, Math.min(1, confidence)) * 100}%`);
  $("#reasonCopy").textContent = suggestion.reason || "系统尚未给出理由。";
  $("#traceOutput").textContent = JSON.stringify(asset.trace || {}, null, 2);

  const candidates = suggestion.candidates || [];
  $("#candidateList").innerHTML = candidates.length
    ? candidates
        .map(
          (candidate) => `
            <button
              class="candidate-card${candidate.id === suggestion.targetId ? " selected" : ""}"
              type="button"
              data-candidate-id="${escapeHtml(candidate.id)}"
            >
              <span class="candidate-top">
                <strong>${escapeHtml(candidate.title)}</strong>
                <span>${formatPercent(candidate.score)}</span>
              </span>
              <span class="candidate-path">${escapeHtml(candidate.path)}</span>
            </button>
          `,
        )
        .join("")
    : `<div class="candidate-path">当前结果来自已保存的建议；重新运行「结构对齐」可再次查看候选。</div>`;

  state.chosenRelation = state.chosenRelation || suggestion.relation;
  renderRelationPicker();
  renderDecisionTarget();
}

function renderMetrics() {
  $("#metricAssets").textContent = state.metrics.assetCount || 0;
  $("#metricRevisions").textContent = state.metrics.revisionCount || 0;
  $("#metricCorrection").textContent = formatPercent(state.metrics.correctionRate);
}

function renderPending() {
  const badge = $("#pendingBadge");
  badge.hidden = state.pending.length === 0;
  badge.textContent = String(state.pending.length);
  $("#pendingMiniList").innerHTML = state.pending
    .slice(0, 4)
    .map(
      (asset) => `
        <div class="pending-mini" data-pending-id="${escapeHtml(asset.id)}">
          <img src="${escapeHtml(asset.original_url)}" alt="">
          <div>
            <strong>${escapeHtml(asset.title)}</strong>
            <span>${formatPercent(asset.confidence)} · 等待上下文</span>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderRelationPicker() {
  $$(".relation-picker button").forEach((button) => {
    button.classList.toggle("active", button.dataset.relation === state.chosenRelation);
  });
}

function renderDecisionTarget() {
  const relation = state.chosenRelation || state.suggestion?.relation;
  const select = $("#targetSelect");
  const targetField = select.closest(".field");
  const newTitleField = $("#newTitleField");
  if (relation === "待定") {
    targetField.hidden = true;
    newTitleField.hidden = true;
    $("#targetLabel").textContent = "目标节点";
    return;
  }
  targetField.hidden = false;
  newTitleField.hidden = relation !== "新建" && relation !== "扩展";
  $("#targetLabel").textContent =
    relation === "新建" ? "新建在哪个节点下" : relation === "扩展" ? "扩展哪个节点" : "目标节点";
  const nodes =
    relation === "新建"
      ? state.nodes
      : state.nodes.filter((node) => node.node_type !== "course");
  const preferred =
    state.suggestion?.finalTargetId ||
    state.suggestion?.targetId ||
    nodes.find((node) => node.node_type === "course")?.id ||
    nodes[0]?.id;
  select.innerHTML = nodes
    .map(
      (node) => `
        <option value="${escapeHtml(node.id)}" ${node.id === preferred ? "selected" : ""}>
          ${escapeHtml(compactNodeLabel(node))}
        </option>
      `,
    )
    .join("");
  if (relation === "扩展" || relation === "新建") {
    const titleInput = $("#newNodeTitle");
    if (!titleInput.value) titleInput.value = findAsset(state.selectedAssetId)?.title || "";
  }
}

function editorPayload() {
  return {
    title: $("#assetTitleInput").value.trim(),
    summary: $("#summaryInput").value.trim(),
    recognized_text: $("#textInput").value.trim(),
    latex: $("#latexInput").value.trim(),
  };
}

async function saveCurrentContent(showMessage = true) {
  const asset = findAsset(state.selectedAssetId);
  if (!asset) return null;
  const payload = editorPayload();
  const response = await api(`/api/assets/${asset.id}/content`, {
    method: "PATCH",
    body: payload,
  });
  const index = state.assets.findIndex((item) => item.id === asset.id);
  if (index >= 0) state.assets[index] = response;
  if (showMessage) showToast("文字与公式已保存");
  return response;
}

async function runAlignment(showOverlay = true) {
  const asset = findAsset(state.selectedAssetId);
  if (!asset) return;
  try {
    if (showOverlay) showLoading("正在召回候选，并检查它该长在哪…");
    const payload = editorPayload();
    if (!payload.summary && !payload.recognized_text && !payload.latex) {
      throw new Error("请先补一句摘要、文字或 LaTeX");
    }
    const updated = await api(`/api/assets/${asset.id}/content`, {
      method: "PATCH",
      body: payload,
    });
    const index = state.assets.findIndex((item) => item.id === asset.id);
    if (index >= 0) state.assets[index] = updated;
    const result = await api(`/api/assets/${asset.id}/align`, { method: "POST" });
    state.suggestion = result.suggestion;
    state.suggestionShownAt = Date.now();
    state.chosenRelation = result.suggestion.relation;
    await loadBootstrap();
    const fresh = findAsset(asset.id);
    if (fresh) {
      state.selectedAssetId = fresh.id;
      state.selectedNodeId = fresh.node_id;
      state.suggestion = result.suggestion;
      renderReview();
      renderTree();
      renderContext();
      setView("workspace");
    }
    const band = result.suggestion.thresholdBand;
    if (band === "auto") showToast("高置信：已自动落位，并保留了可修改入口");
    else if (band === "review") showToast("中等置信：已给出位置，等你确认");
    else showToast("缺少上下文：已先放进未归类箱");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    hideLoading();
  }
}

async function loadSample(sampleId) {
  try {
    showLoading("正在读取匿名样张与版面分区…");
    let asset = state.assets.find((item) => item.sample_id === sampleId);
    if (!asset) {
      const body = new FormData();
      body.append("sample_id", sampleId);
      asset = await api("/api/assets", { method: "POST", body });
      await loadBootstrap();
      asset = findAsset(asset.id) || asset;
    }
    selectAsset(asset.id);
    if (!asset.suggested_relation) {
      await runAlignment(true);
    } else {
      hideLoading();
    }
  } catch (error) {
    hideLoading();
    showToast(error.message, "error");
  }
}

async function uploadFile(file) {
  if (!file) return;
  try {
    showLoading("正在保留原图，并准备离线人工确认…");
    const body = new FormData();
    body.append("file", file);
    const asset = await api("/api/assets", { method: "POST", body });
    await loadBootstrap();
    selectAsset(asset.id);
    showToast("原图已保存在本机。离线模式不会伪造识别文字。");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    hideLoading();
    $("#fileInput").value = "";
    $("#heroFileInput").value = "";
  }
}

async function submitDecision(event) {
  event.preventDefault();
  const asset = findAsset(state.selectedAssetId);
  if (!asset || !state.suggestion) return;
  const relation = state.chosenRelation;
  const targetId = relation === "待定" ? null : $("#targetSelect").value || null;
  const newTitle = $("#newNodeTitle").value.trim();
  if (relation !== "待定" && !targetId) {
    showToast("请选择目标节点", "error");
    return;
  }
  if ((relation === "新建" || relation === "扩展") && !newTitle) {
    showToast("请给新节点起一个标题", "error");
    return;
  }
  try {
    showLoading("正在记录你的决定…");
    const result = await api(`/api/assets/${asset.id}/decision`, {
      method: "POST",
      body: {
        relation,
        target_id: targetId,
        new_node_title: newTitle || null,
        time_to_decide_ms: Date.now() - state.suggestionShownAt,
      },
    });
    await loadBootstrap();
    state.selectedAssetId = result.asset.id;
    state.selectedNodeId = result.asset.node_id;
    state.suggestion = null;
    state.chosenRelation = null;
    renderReview();
    renderContext();
    renderTree();
    showToast("位置已更新，这次修正已写入修订记录");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    hideLoading();
  }
}

async function moveNode(sourceId, targetId) {
  if (!sourceId || !targetId || sourceId === targetId) return;
  try {
    await api(`/api/nodes/${sourceId}`, {
      method: "PATCH",
      body: { parent_id: targetId, time_to_decide_ms: 0 },
    });
    await loadBootstrap();
    renderTree();
    renderContext();
    showToast("节点已改挂，并留下修订记录");
  } catch (error) {
    showToast(error.message, "error");
    renderTree();
  }
}

function openPendingModal() {
  if (!state.pending.length) {
    showToast("未归类箱还是空的");
    return;
  }
  $("#pendingModal").hidden = false;
  $("#pendingModalList").innerHTML = state.pending
    .map(
      (asset, index) => `
        <label class="pending-option">
          <input type="checkbox" value="${escapeHtml(asset.id)}" ${index < 5 ? "checked" : ""}>
          <img src="${escapeHtml(asset.original_url)}" alt="">
          <span>
            <strong>${escapeHtml(asset.title)}</strong>
            <span>${escapeHtml(truncate(asset.summary, 60))}</span>
          </span>
        </label>
      `,
    )
    .join("");
  renderBatchTargets();
}

function closePendingModal() {
  $("#pendingModal").hidden = true;
}

function renderBatchTargets() {
  const relation = $("#batchRelation").value;
  $("#batchTitleField").hidden = relation === "挂载";
  const nodes = relation === "挂载" ? state.nodes.filter((node) => node.node_type !== "course") : state.nodes;
  $("#batchTarget").innerHTML = nodes
    .map(
      (node) => `
        <option value="${escapeHtml(node.id)}">
          ${escapeHtml(node.path || node.title)}
        </option>
      `,
    )
    .join("");
}

async function submitBatchDecision(event) {
  event.preventDefault();
  const selected = $$("#pendingModalList input:checked").map((input) => input.value);
  if (selected.length < 1 || selected.length > 5) {
    showToast("请选择 1–5 条内容", "error");
    return;
  }
  const relation = $("#batchRelation").value;
  const title = $("#batchNewTitle").value.trim();
  if (relation !== "挂载" && !title) {
    showToast("请填写新节点标题", "error");
    return;
  }
  try {
    showLoading("正在把这一组内容一次归位…");
    await api("/api/pending/batch-decision", {
      method: "POST",
      body: {
        asset_ids: selected,
        relation,
        target_id: $("#batchTarget").value || null,
        new_node_title: title || null,
        time_to_decide_ms: 0,
      },
    });
    closePendingModal();
    state.selectedAssetId = null;
    state.suggestion = null;
    await loadBootstrap();
    setView("workspace");
    showToast(`已一次确认 ${selected.length} 条，并分别写入修订记录`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    hideLoading();
  }
}

async function search(query) {
  const clean = query.trim();
  if (!clean) {
    $("#searchResults").innerHTML = `
      <div class="empty-state">
        <span class="empty-mark">⌕</span>
        <strong>先输入一个记忆碎片</strong>
        <p>可以是一个词、一个符号，也可以是一章的名字。</p>
      </div>
    `;
    return;
  }
  try {
    setView("search");
    $("#searchInput").value = clean;
    $("#searchPageInput").value = clean;
    const result = await api(`/api/search?q=${encodeURIComponent(clean)}`);
    renderSearchResults(result.query, result.results);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderSearchResults(query, results) {
  const container = $("#searchResults");
  if (!results.length) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="empty-mark">∅</span>
        <strong>还没有能接住「${escapeHtml(query)}」的内容</strong>
        <p>先载入一张相关样张，再回来试一次。</p>
      </div>
    `;
    return;
  }
  container.innerHTML = results
    .map(
      (result, index) => `
        <article class="result-row" data-result-asset="${escapeHtml(result.assetId || "")}" data-result-node="${escapeHtml(result.nodeId || "")}">
          <span class="result-index">0${index + 1}</span>
          <div class="result-copy">
            <strong>${escapeHtml(result.title)}</strong>
            <span class="result-path">${escapeHtml(result.path)}</span>
            <div class="result-reasons">
              ${result.reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}
            </div>
          </div>
          <span class="result-score">${formatPercent(result.score)}<small>${result.type === "asset" ? "原文命中" : "节点相关"}</small></span>
        </article>
      `,
    )
    .join("");
}

async function openSearchResult(assetId, nodeId) {
  if (assetId) {
    state.suggestion = null;
    selectAsset(assetId);
    return;
  }
  state.selectedNodeId = nodeId;
  renderTree();
  setView("graph");
  showToast(`「${nodeTitle(nodeId)}」目前还没有关联素材，可在关系图里查看位置`);
}

async function loadGraph() {
  try {
    const graph = await api("/api/graph");
    state.graph = graph;
    renderGraph(graph);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function renderGraph(graph) {
  const nodes = graph.nodes || [];
  if (!nodes.length) {
    $("#graphCanvas").innerHTML = `<div class="empty-state"><strong>知识树还是空的</strong></div>`;
    return;
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const children = new Map();
  for (const node of nodes) {
    if (!children.has(node.parent_id)) children.set(node.parent_id, []);
    children.get(node.parent_id).push(node);
  }
  for (const items of children.values()) {
    items.sort((a, b) => a.sort_order - b.sort_order || a.title.localeCompare(b.title, "zh-CN"));
  }

  function depthOf(node) {
    let depth = 0;
    let cursor = node;
    const seen = new Set();
    while (cursor?.parent_id && !seen.has(cursor.parent_id)) {
      seen.add(cursor.parent_id);
      cursor = byId.get(cursor.parent_id);
      depth += 1;
    }
    return depth;
  }

  const levels = new Map();
  for (const node of nodes) {
    const depth = depthOf(node);
    if (!levels.has(depth)) levels.set(depth, []);
    levels.get(depth).push(node);
  }
  for (const items of levels.values()) {
    items.sort((a, b) => a.sort_order - b.sort_order || a.title.localeCompare(b.title, "zh-CN"));
  }

  const maxDepth = Math.max(...levels.keys());
  const width = 1000;
  const height = Math.max(560, 150 + (maxDepth + 1) * 122);
  const positions = new Map();
  for (const [depth, items] of levels.entries()) {
    const gap = width / (items.length + 1);
    items.forEach((node, index) => {
      positions.set(node.id, {
        x: gap * (index + 1),
        y: 58 + depth * 122,
      });
    });
  }

  const parentPaths = (graph.parentEdges || [])
    .map((edge) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return "";
      const midY = (source.y + 18 + target.y - 18) / 2;
      return `<path class="tree-link" d="M ${source.x} ${source.y + 19} C ${source.x} ${midY}, ${target.x} ${midY}, ${target.x} ${target.y - 19}"></path>`;
    })
    .join("");

  const crossPaths = (graph.crossEdges || [])
    .map((edge) => {
      const source = positions.get(edge.source_id);
      const target = positions.get(edge.target_id);
      if (!source || !target) return "";
      const bend = Math.abs(source.x - target.x) * 0.28 + 40;
      return `
        <path class="cross-link" d="M ${source.x} ${source.y} C ${source.x + bend} ${source.y - 60}, ${target.x - bend} ${target.y - 60}, ${target.x} ${target.y}"></path>
        <title>${escapeHtml(edge.reason || "跨章节关系")}</title>
      `;
    })
    .join("");

  const nodeMarkup = nodes
    .map((node) => {
      const position = positions.get(node.id);
      const boxWidth = node.node_type === "course" ? 120 : node.node_type === "chapter" ? 142 : 126;
      const label = truncate(node.title, node.node_type === "chapter" ? 13 : 12);
      const active = node.id === state.selectedNodeId ? " active" : "";
      return `
        <g class="graph-node ${escapeHtml(node.node_type)}${active}" data-graph-node="${escapeHtml(node.id)}" transform="translate(${position.x - boxWidth / 2} ${position.y - 18})">
          <rect width="${boxWidth}" height="36"></rect>
          <text x="${boxWidth / 2}" y="22">${escapeHtml(label)}</text>
          <title>${escapeHtml(node.path || node.title)}</title>
        </g>
      `;
    })
    .join("");

  $("#graphCanvas").innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="LOOM 知识关系图">
      <g>${parentPaths}${crossPaths}</g>
      <g>${nodeMarkup}</g>
    </svg>
  `;
}

async function openGraphNode(nodeId) {
  const asset = state.assets.find((item) => item.node_id === nodeId);
  if (asset) {
    state.suggestion = null;
    selectAsset(asset.id);
  } else {
    state.selectedNodeId = nodeId;
    renderGraph(state.graph);
    showToast(`「${nodeTitle(nodeId)}」暂无已关联素材`);
  }
}

async function loadRevisions() {
  try {
    const data = await api("/api/revisions");
    const revisions = data.revisions || [];
    $("#revisionSummary").innerHTML = `
      <div><strong>${revisions.length}</strong><span>全部记录</span></div>
      <div><strong>${state.metrics.correctedCount || 0}</strong><span>真实修正</span></div>
      <div><strong>${formatPercent(state.metrics.correctionRate)}</strong><span>修正率</span></div>
    `;
    if (!revisions.length) {
      $("#revisionList").innerHTML = `
        <div class="empty-state">
          <span class="empty-mark">↺</span>
          <strong>还没有修订记录</strong>
          <p>确认位置、改挂节点后，这里会留下可解释的轨迹。</p>
        </div>
      `;
      return;
    }
    $("#revisionList").innerHTML = revisions
      .map((revision) => {
        const before = revision.before || {};
        const after = revision.after || {};
        const beforeLabel =
          before.relation && before.relation !== "待定"
            ? `${before.relation} → ${nodeTitle(before.target_id)}`
            : "未归类 / 手动处理";
        const afterLabel =
          after.relation && after.relation !== "待定"
            ? `${after.relation} → ${nodeTitle(after.target_id)}`
            : after.parent_id
              ? `改挂到 ${nodeTitle(after.parent_id)}`
              : "进入未归类箱";
        return `
          <article class="revision-row">
            <span class="revision-time">${escapeHtml(formatDate(revision.created_at))}</span>
            <div class="revision-detail">
              <strong>${escapeHtml(revision.asset_title || actionLabels[revision.action] || revision.action)}</strong>
              <div class="revision-change">${escapeHtml(beforeLabel)} <b>→</b> ${escapeHtml(afterLabel)} · ${revision.time_to_decide_ms || 0} ms</div>
            </div>
            <span class="revision-pill${revision.isCorrection ? " corrected" : ""}">
              ${revision.isCorrection ? "修正了系统建议" : actionLabels[revision.action] || "已记录"}
            </span>
          </article>
        `;
      })
      .join("");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function bindEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  $("#brandButton").addEventListener("click", () => {
    state.selectedAssetId = null;
    state.selectedNodeId = null;
    state.suggestion = null;
    setView("workspace");
    renderContext();
  });

  $("#sampleGrid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-sample-id]");
    if (card) loadSample(card.dataset.sampleId);
  });

  $("#openFirstSample").addEventListener("click", () => {
    if (state.samples[0]) loadSample(state.samples[0].id);
  });

  for (const input of [$("#fileInput"), $("#heroFileInput")]) {
    input.addEventListener("change", () => uploadFile(input.files?.[0]));
  }

  $("#saveContentButton").addEventListener("click", async () => {
    try {
      showLoading("正在保存你对文字与公式的修改…");
      await saveCurrentContent();
      await loadBootstrap();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      hideLoading();
    }
  });

  $("#alignButton").addEventListener("click", () => runAlignment(true));

  $("#tree").addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-id]");
    if (toggle) {
      event.stopPropagation();
      const nodeId = toggle.dataset.toggleId;
      if (state.expanded.has(nodeId)) state.expanded.delete(nodeId);
      else state.expanded.add(nodeId);
      renderTree();
      return;
    }
    const button = event.target.closest("[data-node-id]");
    if (!button) return;
    const linked = state.assets.find((asset) => asset.node_id === button.dataset.nodeId);
    if (linked) {
      state.suggestion = null;
      selectAsset(linked.id);
    } else {
      state.selectedNodeId = button.dataset.nodeId;
      renderTree();
      showToast(`「${nodeTitle(button.dataset.nodeId)}」还没有关联素材`);
    }
  });

  let draggedNodeId = null;
  $("#tree").addEventListener("dragstart", (event) => {
    const node = event.target.closest("[data-node-id]");
    if (!node || node.dataset.type === "course") {
      event.preventDefault();
      return;
    }
    draggedNodeId = node.dataset.nodeId;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedNodeId);
  });
  $("#tree").addEventListener("dragover", (event) => {
    const node = event.target.closest("[data-node-id]");
    if (!node || node.dataset.nodeId === draggedNodeId) return;
    event.preventDefault();
    $$(".tree-node.drag-over").forEach((item) => item.classList.remove("drag-over"));
    node.classList.add("drag-over");
  });
  $("#tree").addEventListener("dragleave", (event) => {
    event.target.closest("[data-node-id]")?.classList.remove("drag-over");
  });
  $("#tree").addEventListener("drop", (event) => {
    const node = event.target.closest("[data-node-id]");
    if (!node) return;
    event.preventDefault();
    const source = draggedNodeId || event.dataTransfer.getData("text/plain");
    $$(".tree-node.drag-over").forEach((item) => item.classList.remove("drag-over"));
    moveNode(source, node.dataset.nodeId);
    draggedNodeId = null;
  });

  $$(".relation-picker button").forEach((button) => {
    button.addEventListener("click", () => {
      state.chosenRelation = button.dataset.relation;
      renderRelationPicker();
      renderDecisionTarget();
    });
  });

  $("#candidateList").addEventListener("click", (event) => {
    const card = event.target.closest("[data-candidate-id]");
    if (!card) return;
    state.chosenRelation = "挂载";
    renderRelationPicker();
    renderDecisionTarget();
    $("#targetSelect").value = card.dataset.candidateId;
    $$(".candidate-card").forEach((item) => item.classList.toggle("selected", item === card));
  });

  $("#decisionForm").addEventListener("submit", submitDecision);

  $("#pendingButton").addEventListener("click", openPendingModal);
  $("#openPendingFromRail").addEventListener("click", openPendingModal);
  $("#closePendingModal").addEventListener("click", closePendingModal);
  $("#pendingModal").addEventListener("click", (event) => {
    if (event.target === $("#pendingModal")) closePendingModal();
  });
  $("#pendingModalList").addEventListener("change", (event) => {
    const checked = $$("#pendingModalList input:checked");
    if (checked.length > 5) {
      event.target.checked = false;
      showToast("一次最多确认 5 条", "error");
    }
  });
  $("#batchRelation").addEventListener("change", renderBatchTargets);
  $("#batchForm").addEventListener("submit", submitBatchDecision);

  $("#searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    search($("#searchInput").value);
  });
  $("#searchPageButton").addEventListener("click", () => search($("#searchPageInput").value));
  $("#searchPageInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") search(event.currentTarget.value);
  });
  $("#searchResults").addEventListener("click", (event) => {
    const row = event.target.closest("[data-result-asset]");
    if (!row) return;
    openSearchResult(row.dataset.resultAsset, row.dataset.resultNode);
  });

  $("#graphCanvas").addEventListener("click", (event) => {
    const node = event.target.closest("[data-graph-node]");
    if (node) openGraphNode(node.dataset.graphNode);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#pendingModal").hidden) closePendingModal();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#searchInput").focus();
    }
  });

  document.addEventListener("dragover", (event) => {
    if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
  });
  document.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    uploadFile(event.dataTransfer.files[0]);
  });
}

async function init() {
  bindEvents();
  try {
    await loadBootstrap();
    renderContext();
  } catch (error) {
    $("#treeSkeleton").hidden = true;
    $("#tree").innerHTML = `<div class="empty-state"><strong>服务暂时不可用</strong><p>${escapeHtml(error.message)}</p></div>`;
    showToast(error.message, "error");
  }
}

init();
