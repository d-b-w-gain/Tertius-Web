const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
const mm = (value) => value ? `${new Intl.NumberFormat("en-AU", { maximumFractionDigits: 1 }).format(value)} mm` : "—";
const number = (value) => new Intl.NumberFormat("en-AU", { maximumFractionDigits: 1 }).format(value ?? 0);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({ message: "Invalid server response" }));
  if (!response.ok) throw new Error(body.message || `Request failed (${response.status})`);
  return body;
}

function readinessRow(done, title, detail) {
  return `<li class="${done ? "done" : "todo"}"><span>${done ? "✓" : "○"}</span><div><strong>${esc(title)}</strong><p>${esc(detail)}</p></div></li>`;
}

function renderStatus(status) {
  $("#api-product").textContent = status.product;
  $("#api-business").textContent = status.business_unit;
  $("#api-name").textContent = status.api;

  const c = status.credentials;
  $("#readiness").innerHTML = [
    readinessRow(c.subscription_key, "Approved BBC product subscription", c.subscription_key ? "Subscription key is mounted securely." : "Create a named BBC Purchase Orders subscription; BlueScope admin must approve it."),
    readinessRow(c.oauth_client_id && c.oauth_client_secret && c.oauth_resource, "OAuth organisation onboarding", c.oauth_client_id && c.oauth_client_secret && c.oauth_resource ? "Client credentials and resource are mounted securely." : "IntegrationSupport@bluescope.com must issue client_id, client_secret, and resource for your organisation and buyer identifiers."),
    readinessRow(false, "Lysaght account and catalogue identifiers", "Needed to establish pricing and convert BoM descriptions into accepted order-line identifiers."),
    readinessRow(false, "BlueScope EDI test approval", "Validate the BBC v2 GS1 schema and examples before any production order is enabled."),
  ].join("");

  const badge = $("#overall-badge");
  badge.textContent = status.order_ready ? "Credentials mounted · onboarding still required" : "Not ready for electronic ordering";
  badge.className = `badge ${status.order_ready ? "partial" : "waiting"}`;
  $("#probe").disabled = !status.probe_ready;
  if (!c.subscription_key) {
    $("#probe").textContent = "Probe without key";
  }
}

function renderBom(bom) {
  $("#bom-count").textContent = `${bom.items.length} candidate lines`;
  $("#bom-source").textContent = bom.available
    ? (bom.snapshot?.design_name || bom.snapshot?.filename || "Latest BoM snapshot")
    : bom.reason;
  $("#mapping-count").textContent = `${bom.items.length} lines`;

  $("#bom-items").innerHTML = bom.items.length ? bom.items.map((item) => `
    <tr>
      <td>${esc(item.family)}</td>
      <td><strong>${esc(item.part_number)}</strong><span class="subline">${esc(item.description)}</span></td>
      <td>${number(item.quantity)} ${esc(item.unit)}</td>
      <td>${mm(item.length_mm)}</td>
      <td>${esc([item.finish, item.colour, item.grade].filter(Boolean).join(" · ") || "—")}</td>
      <td><span class="mapping ${item.mapping.startsWith("Lysaght") ? "likely" : "confirm"}">${esc(item.mapping)}</span></td>
    </tr>`).join("") : `<tr><td colspan="6">${esc(bom.reason || "No likely Lysaght lines were found.")}</td></tr>`;

  $("#stock-plans").innerHTML = bom.stock_plans.length ? bom.stock_plans.map((plan) => `
    <article class="stock-card">
      <div class="stock-title"><h3>${esc(plan.profile)}</h3><strong>${plan.stock_bars} × 9 m bars</strong></div>
      <p>${plan.cut_count} pieces from ${plan.stock_bars} full stock lengths.</p>
      <details>
        <summary>Show proposed cuts</summary>
        <ol>${plan.bars.map((bar) => `<li><strong>Bar ${bar.number}:</strong> ${bar.cuts_mm.map(mm).join(" + ")} <span>${mm(bar.offcut_mm)} offcut</span></li>`).join("")}</ol>
      </details>
    </article>`).join("") : `<p class="muted">No Cee profile cuts were found in the latest BoM.</p>`;
}

$("#probe").addEventListener("click", async () => {
  const button = $("#probe");
  const result = $("#probe-result");
  button.disabled = true;
  result.className = "result pending";
  result.textContent = "Contacting the BlueScope API gateway…";
  try {
    const response = await api("/api/connectivity", { method: "POST" });
    result.className = `result ${response.ok ? "success" : "error"}`;
    result.textContent = `${response.message}${response.upstream_status ? ` HTTP ${response.upstream_status}.` : ""}`;
  } catch (error) {
    result.className = "result error";
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

Promise.all([api("/api/status"), api("/api/bom")])
  .then(([status, bom]) => {
    renderStatus(status);
    renderBom(bom);
  })
  .catch((error) => {
    $("#overall-badge").textContent = `Could not load: ${error.message}`;
    $("#overall-badge").className = "badge error";
  });
