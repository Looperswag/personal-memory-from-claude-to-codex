const svg = document.querySelector("#graph");
const detailPanel = document.querySelector("#detailPanel");
const results = document.querySelector("#results");
const searchInput = document.querySelector("#searchInput");
const syncState = document.querySelector("#syncState");
const activeTypes = new Set(["project", "topic", "design", "doc", "skill"]);

let graph = { nodes: [], edges: [], event_id: 0 };
let selectedId = "";
let animation = 0;

const colors = {
  project: "#2f6f4e",
  topic: "#a36a22",
  design: "#3d5f8f",
  doc: "#9a3d65",
  file: "#6f7480",
  skill: "#a5463c",
  profile: "#202124",
};

async function requestJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
}

async function loadGraph() {
  syncState.textContent = "Syncing";
  graph = await requestJson(`/api/graph?since=${graph.event_id || 0}`);
  document.querySelector("#nodeCount").textContent = graph.nodes.length;
  document.querySelector("#edgeCount").textContent = graph.edges.length;
  document.querySelector("#eventId").textContent = graph.event_id;
  syncState.textContent = "Live";
  layoutGraph();
}

function visibleGraph() {
  const nodes = graph.nodes.filter((node) => activeTypes.has(node.type) || node.id === selectedId || node.type === "profile");
  const ids = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  return { nodes: nodes.map((node) => ({ ...node })), edges };
}

function layoutGraph() {
  cancelAnimationFrame(animation);
  const view = visibleGraph();
  const width = svg.clientWidth || 900;
  const height = svg.clientHeight || 640;
  const byId = new Map(view.nodes.map((node) => [node.id, node]));

  const buckets = {
    profile: view.nodes.filter((node) => node.type === "profile"),
    topic: view.nodes.filter((node) => node.type === "topic"),
    project: view.nodes.filter((node) => node.type === "project"),
    other: view.nodes.filter((node) => !["profile", "topic", "project"].includes(node.type)),
  };
  placeBucket(buckets.profile, width / 2, height / 2, 0, 0);
  placeBucket(buckets.topic, width / 2, height / 2, Math.min(width, height) * 0.18, -Math.PI / 2);
  placeBucket(buckets.project, width / 2, height / 2, Math.min(width, height) * 0.33, -Math.PI / 2.3);
  placeBucket(buckets.other, width / 2, height / 2, Math.min(width, height) * 0.44, -Math.PI / 2.7);

  render(view, byId);
}

function placeBucket(nodes, cx, cy, radiusValue, startAngle) {
  nodes.sort((a, b) => a.title.localeCompare(b.title));
  nodes.forEach((node, index) => {
    const angle = startAngle + (Math.PI * 2 * index) / Math.max(1, nodes.length);
    const jitter = (hash(node.id) % 17) - 8;
    node.x = cx + Math.cos(angle) * (radiusValue + jitter);
    node.y = cy + Math.sin(angle) * (radiusValue + jitter);
  });
}

function render(view, byId) {
  svg.replaceChildren();
  const edgeLayer = el("g", { class: "links" });
  const nodeLayer = el("g", { class: "nodes" });
  svg.append(edgeLayer, nodeLayer);

  for (const edge of view.edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    edgeLayer.append(el("line", { class: "link", x1: source.x, y1: source.y, x2: target.x, y2: target.y }));
  }

  for (const node of view.nodes) {
    const group = el("g", { class: `node ${node.id === selectedId ? "selected" : ""}`, tabindex: "0" });
    group.setAttribute("transform", `translate(${node.x},${node.y})`);
    group.addEventListener("click", () => selectNode(node.id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter") selectNode(node.id);
    });
    group.append(el("circle", { r: radius(node), fill: color(node) }));
    if (shouldLabel(node)) {
      const label = el("text", { x: radius(node) + 7, y: 4 });
      label.textContent = node.title.length > 22 ? `${node.title.slice(0, 21)}...` : node.title;
      group.append(label);
    }
    group.append(el("title"));
    group.querySelector("title").textContent = node.title;
    nodeLayer.append(group);
  }
}

async function selectNode(id) {
  selectedId = id;
  layoutGraph();
  const detail = await requestJson(`/api/node/${encodeURIComponent(id)}`);
  renderDetail(detail);
}

function renderDetail(detail) {
  const node = detail.node;
  detailPanel.replaceChildren();
  detailPanel.append(textEl("span", node.type, "badge"));
  detailPanel.append(textEl("h2", node.title));
  if (node.summary) detailPanel.append(textEl("p", node.summary, "summary"));

  const meta = document.createElement("dl");
  meta.className = "meta";
  const rows = { source: node.source_path, updated: node.updated_at, size: node.size, ...node.meta };
  Object.entries(rows).forEach(([key, value]) => {
    if (value === "" || value === undefined || value === null) return;
    const row = document.createElement("div");
    row.append(textEl("dt", key), textEl("dd", String(value)));
    meta.append(row);
  });
  detailPanel.append(meta);

  if (detail.neighbors.length) {
    detailPanel.append(textEl("h3", "Related"));
    const neighbors = document.createElement("div");
    neighbors.className = "neighbors";
    detail.neighbors.slice(0, 16).forEach((neighbor) => {
      const button = textEl("button", neighbor.title);
      button.addEventListener("click", () => selectNode(neighbor.id));
      neighbors.append(button);
    });
    detailPanel.append(neighbors);
  }
}

async function runSearch() {
  const query = searchInput.value.trim();
  const rows = query ? await requestJson(`/api/search?q=${encodeURIComponent(query)}`) : [];
  results.replaceChildren();
  rows.forEach((node) => {
    const button = document.createElement("button");
    button.className = "result";
    button.innerHTML = `<b></b><span></span>`;
    button.querySelector("b").textContent = node.title;
    button.querySelector("span").textContent = `${node.type}${node.updated_at ? ` · ${node.updated_at}` : ""}`;
    button.addEventListener("click", () => selectNode(node.id));
    results.append(button);
  });
}

function color(node) {
  if (node.type === "project" && node.meta?.status === "blocked") return "#a5463c";
  if (node.type === "project" && node.meta?.status === "archived") return "#6f7480";
  return colors[node.type] || "#202124";
}

function radius(node) {
  if (node.type === "profile") return 18;
  if (node.type === "project") return 13;
  if (node.type === "topic") return 10;
  return 8;
}

function shouldLabel(node) {
  return node.id === selectedId || ["profile", "project", "topic"].includes(node.type);
}

function hash(value) {
  let out = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    out ^= value.charCodeAt(i);
    out = Math.imul(out, 16777619);
  }
  return out >>> 0;
}

function el(name, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function textEl(name, text, className = "") {
  const node = document.createElement(name);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    const type = button.dataset.type;
    if (activeTypes.has(type)) activeTypes.delete(type);
    else activeTypes.add(type);
    button.classList.toggle("active", activeTypes.has(type));
    layoutGraph();
  });
});

document.querySelector("#showAll").addEventListener("click", () => {
  ["project", "topic", "design", "doc", "file", "skill"].forEach((type) => activeTypes.add(type));
  document.querySelectorAll(".filter").forEach((button) => button.classList.add("active"));
  layoutGraph();
});

document.querySelector("#resetView").addEventListener("click", layoutGraph);
searchInput.addEventListener("input", () => {
  clearTimeout(searchInput._timer);
  searchInput._timer = setTimeout(runSearch, 160);
});
window.addEventListener("resize", layoutGraph);

setInterval(async () => {
  try {
    const event = await requestJson(`/api/events?since=${graph.event_id || 0}`);
    if (event.changed) await loadGraph();
  } catch (error) {
    syncState.textContent = "Offline";
  }
}, 2200);

loadGraph().catch(() => {
  syncState.textContent = "Offline";
});
