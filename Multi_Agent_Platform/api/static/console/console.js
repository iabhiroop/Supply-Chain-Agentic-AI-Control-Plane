(() => {
  const $ = (id) => document.getElementById(id);
  const views = {
    team: ["Team", "Company specialists, orchestrator, and external supplier agents."],
    jobs: ["Jobs", "One purchase job on a stage rail. Run the orchestrator from here."],
    inbox: ["B2B inbox", "Partner mail only — buyer facade, vendors, EDI. Specialists do not email each other."],
    hitl: ["Needs you", "Jobs waiting on a human, or delay / match breaks."],
    memory: ["Memory", "What agents wrote after a turn so the next run can learn."],
    playbooks: ["Playbooks", "PR-1001 golden, PR-1002 rebook, PR-1003 HITL delay."],
  };
  const RAIL = [
    ["INTENT", ["INTENT"]],
    ["SOURCING", ["SOURCING"]],
    ["PO", ["PO_ISSUED", "PO_PENDING_APPROVAL"]],
    ["DISPATCH", ["DISPATCH_READY", "IN_TRANSIT"]],
    ["RECEIVED", ["RECEIVED"]],
    ["MATCH", ["INVOICE_CAPTURED", "MATCHED"]],
    ["SETTLED", ["SETTLED"]],
  ];

  let overview = {};
  let team = {};
  let selectedId = null;
  let detail = null;
  let selectedAgent = "Sourcing_Agent";
  let sessionId = null;
  let copilotBusy = false;
  let busy = false;

  function fmt(v) {
    return v === null || v === undefined || v === "" ? "—" : v;
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]
    ));
  }
  function pill(v) {
    const cls = String(v || "").toLowerCase().replace(/\s+/g, "_");
    return `<span class="pill ${cls}">${esc(fmt(v))}</span>`;
  }
  function table(headers, body) {
    if (!body) return `<div class="empty">None.</div>`;
    return `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function selectedTx() {
    return (overview.transactions || []).find((t) => t.transaction_id === selectedId) || null;
  }

  function updateJobChip() {
    const tx = (detail && detail.transaction) || selectedTx() || {};
    $("job-chip-pr").textContent = tx.pr_id || "Select a job or run a playbook";
    $("job-chip-stage").className = "pill " + String(tx.stage || "intent").toLowerCase();
    $("job-chip-stage").textContent = tx.stage || "—";
  }

  function isHitl(t) {
    return t.kind === "model" || t.status === "AWAITING_HUMAN" || ["AWAITING_HUMAN", "PO_PENDING_APPROVAL"].includes(t.stage);
  }
  function isException(t) {
    return t.status === "EXCEPTION" || t.stage === "EXCEPTION";
  }
  function isModelHold(t) {
    return t && (t.kind === "model" || (t.request_id && (t.options || []).length));
  }
  function modelButtons(t) {
    const id = t.decide_id || t.transaction_id || t.request_id;
    return (t.options || []).map((opt) => {
      const cls = opt.id === t.recommended_option ? "primary" : "ghost";
      return `<button class="${cls}" data-decide="${esc(id)}|answer|${esc(opt.id)}">${esc(opt.label || opt.id)}</button>`;
    }).join("");
  }

  function legalQuote(q, lastPrice, maxPct) {
    const price = Number(q.unit_price);
    const last = Number(lastPrice) || 0;
    if (!last) return true;
    return Math.abs(price - last) / last * 100 <= Number(maxPct || 5);
  }

  function renderTeam() {
    const members = team.members || [];
    const k = overview.kpis || {};
    const kpis = `
      <div class="kpis">
        <div class="kpi"><div class="label">Open jobs</div><div class="value">${fmt(k.open_transactions)}</div></div>
        <div class="kpi warn"><div class="label">Needs you</div><div class="value">${fmt(k.hitl)}</div></div>
        <div class="kpi danger"><div class="label">Exceptions</div><div class="value">${fmt(k.exceptions)}</div></div>
        <div class="kpi"><div class="label">Unread partner mail</div><div class="value">${fmt(k.unread_inbox)}</div></div>
      </div>`;
    const card = (m) => {
      const run = m.last_run || {};
      const mail = m.mail ? pill(m.mail) : "";
      const unread = m.unread ? `<span class="pill warn">${m.unread} unread</span>` : "";
      return `
        <button type="button" class="agent-card ${selectedAgent === m.id ? "active" : ""}" data-agent="${esc(m.id)}">
          <div class="kind">${esc(m.kind)} ${mail} ${unread}</div>
          <h4>${esc(m.title)}</h4>
          <p>${esc(m.job)}</p>
          <p class="must-not">${esc(m.must_not)}</p>
          <p class="muted">${esc(m.owns_when)}</p>
          <div class="turn">
            ${run.status ? `${pill(run.status)} ${esc(run.summary || "No summary yet.")}` : `<span class="muted">No turn recorded yet.</span>`}
          </div>
        </button>`;
    };
    const company = members.filter((m) => m.kind === "company");
    const external = members.filter((m) => m.kind !== "company");
    const focus = members.find((m) => m.id === selectedAgent) || company[0];
    const run = (focus && focus.last_run) || {};
    const loop = focus && focus.kind === "company" && focus.agent_name
      ? `<div class="loop"><span>Sense</span><span>Decide</span><span>Act</span><span>Verify ${run.verified ? "✓" : ""}</span><span>Learn</span></div>`
      : "";
    $("view-team").innerHTML = `
      ${kpis}
      <p class="group-title">Company</p>
      <div class="card-grid">${company.map(card).join("")}</div>
      <p class="group-title">External (B2B)</p>
      <div class="card-grid">${external.map(card).join("")}</div>
      <div class="panel" style="margin-top:16px">
        <h3>${esc((focus && focus.title) || "Agent")} — last turn</h3>
        <div class="body">
          <p>${esc((focus && focus.job) || "")}</p>
          ${loop}
          ${run.findings && run.findings.length ? `<p><strong>Sensed / found</strong></p><ul>${run.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
          ${run.actions_taken && run.actions_taken.length ? `<p><strong>Acted</strong></p><ul>${run.actions_taken.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
          ${run.recommendation ? `<p><strong>Next</strong> ${esc(run.recommendation)}</p>` : ""}
          ${!run.status ? `<div class="empty">Select a specialist. Run a job to see Sense → Act → Verify.</div>` : ""}
          <div class="row">
            ${selectedId ? `<button class="primary" data-run="${esc(selectedId)}">Run selected job</button>` : `<span class="muted">Select a job on Jobs or a playbook first.</span>`}
          </div>
        </div>
      </div>`;
  }

  function railHtml(stage, status) {
    const hold = status === "AWAITING_HUMAN" || stage === "AWAITING_HUMAN" || stage === "PO_PENDING_APPROVAL";
    const ex = status === "EXCEPTION" || stage === "EXCEPTION";
    let seen = false;
    const cells = RAIL.map(([label, stages]) => {
      const on = stages.includes(stage);
      if (on) seen = true;
      const done = !on && RAIL.findIndex((r) => r[1].includes(stage)) > RAIL.findIndex((r) => r[0] === label);
      let cls = "step";
      if (on && (hold || ex)) cls += " hold";
      else if (on) cls += " on";
      else if (done || (stage === "SETTLED" && label !== "SETTLED" && seen === false)) cls += " done";
      if (stage === "SETTLED" && label !== "SETTLED") cls = "step done";
      return `<div class="${cls}"><div>${label}</div><div class="muted">${on ? esc(stage) : ""}</div></div>`;
    }).join("");
    return `<div class="rail">${cells}</div>`;
  }

  function quotesHtml() {
    const quotes = (detail && detail.quotes) || [];
    const last = (detail && detail.material && detail.material.last_price) || 10;
    const maxPct = (detail && detail.policy && detail.policy.price_deviation_pct) || 5;
    if (!quotes.length) return `<p class="muted">No quotes yet.</p>`;
    return `<ul>${quotes.map((q) => {
      const ok = legalQuote(q, last, maxPct);
      return `<li>${esc(q.vendor_id)} @ ${fmt(q.unit_price)} lead ${fmt(q.lead_days)}d ${ok ? pill("in policy") : pill("out")}</li>`;
    }).join("")}</ul><p class="muted">Last price ${last}. Legal window ±${maxPct}%.</p>`;
  }

  function docsHtml() {
    if (!detail) return "";
    const pr = detail.pr || {};
    const po = detail.po || {};
    const book = detail.booking || {};
    const gr = detail.gr || {};
    const inv = detail.supplier_invoice || {};
    const fr = detail.freight_invoice || {};
    const match = detail.match || {};
    return `
      <p><strong>PR</strong> ${fmt(pr.pr_id)} ${fmt(pr.material_id)} × ${fmt(pr.quantity)} ${fmt(pr.plant_id)}</p>
      ${quotesHtml()}
      <p><strong>PO</strong> ${fmt(po.po_id)} ${fmt(po.vendor_id)} @ ${fmt(po.unit_price)} ${pill(po.status)}</p>
      <p><strong>Booking</strong> ${fmt(book.booking_id)} ${esc(book.carrier_name)} ${pill(book.status)}</p>
      <p><strong>GR</strong> ${fmt(gr.gr_id)} qty ${fmt(gr.quantity)}</p>
      <p><strong>Invoices</strong> supplier ${fmt(inv.invoice_id)} · freight ${fmt(fr.freight_invoice_id)} ${fmt(fr.amount)}</p>
      <p><strong>Match</strong> ${fmt(match.match_id)} variance ${fmt(match.variance_pct)}% ${pill(match.status)}</p>`;
  }

  function agentTimeline() {
    const runs = (detail && detail.agent_runs) || [];
    if (!runs.length) return `<div class="empty">Not run yet. Run orchestrator to see specialists on this job.</div>`;
    return runs.map((r) => {
      let result = r.result || {};
      if (typeof result === "string") {
        try { result = JSON.parse(result); } catch { result = {}; }
      }
      return `<div class="item"><strong>${esc(r.agent_name)}</strong> ${pill(r.status)}
        <div>${esc(result.summary || "")}</div>
        <div class="muted">Verify ${result.verified ? "yes" : "waiting"} · ${fmt(r.ended_at)}</div></div>`;
    }).join("");
  }

  function renderJobs() {
    const txs = overview.transactions || [];
    if (!selectedId && !txs.length) {
      $("view-jobs").innerHTML = `<div class="empty">No jobs. Seed with python database/seed_execution.py</div>`;
      return;
    }
    const tx = (detail && detail.transaction) || selectedTx() || {};
    const hitlReq = (detail && detail.hitl_request) || {};
    const modelHold = isModelHold(hitlReq) || (tx.stage === "AWAITING_HUMAN" && (tx.context || {}).hitl_kind === "model");
    const hitl = tx.stage === "PO_PENDING_APPROVAL" || tx.status === "AWAITING_HUMAN";
    const poHold = tx.stage === "PO_PENDING_APPROVAL" && !modelHold;
    $("view-jobs").innerHTML = `
      <div class="grid-2">
        <div class="panel">
          <h3>All jobs</h3>
          ${table(["PR", "Stage", "Status", "Material"], txs.map((t) => `
            <tr class="clickable" data-open-tx="${t.transaction_id}">
              <td class="mono">${esc(t.pr_id)}</td>
              <td>${pill(t.stage)}</td>
              <td>${pill(t.status)}</td>
              <td>${fmt(t.material_id)} × ${fmt(t.quantity)}</td>
            </tr>`).join(""))}
        </div>
        <div class="panel">
          <h3>Job canvas</h3>
          <div class="body">
            ${tx.pr_id ? "" : `<div class="empty">Select a job or run a playbook.</div>`}
            ${tx.pr_id ? `
              <p><strong>${esc(tx.pr_id)}</strong> ${pill(tx.stage)} ${pill(tx.status)}</p>
              ${tx.halt_reason ? `<p class="banner">${esc(tx.halt_reason)}</p>` : ""}
              ${railHtml(tx.stage, tx.status)}
              <div class="row">
                <button class="primary" data-run="${esc(tx.transaction_id)}" ${busy ? "disabled" : ""}>${busy ? "Running…" : "Run orchestrator"}</button>
                ${modelHold ? modelButtons({ ...hitlReq, transaction_id: tx.transaction_id, decide_id: tx.transaction_id }) : ""}
                ${hitl && poHold ? `<button class="ghost" data-decide="${esc(tx.transaction_id)}|approve_po">Approve PO</button>` : ""}
                ${hitl && !poHold && !modelHold ? `<button class="ghost" data-decide="${esc(tx.transaction_id)}|approve_exception">Approve exception & rebook</button>
                <button class="ghost" data-decide="${esc(tx.transaction_id)}|cancel">Cancel PO</button>
                <button class="ghost" data-decide="${esc(tx.transaction_id)}|keep_waiting">Keep waiting</button>` : ""}
              </div>
              ${docsHtml()}
              <h3 style="padding:12px 0 0;text-transform:none;letter-spacing:0;font-size:14px;color:var(--ink)">Agent timeline</h3>
              <div class="thread">${agentTimeline()}</div>
            ` : ""}
          </div>
        </div>
      </div>`;
  }

  function inboxGroup(label, rows) {
    const body = (rows || []).map((m) => `
      <tr class="clickable" data-open-tx="${m.transaction_id || ""}">
        <td>${esc(m.subject)}</td>
        <td>${pill((m.parsed && m.parsed.type) || m.channel)}</td>
        <td>${fmt(m.direction)}</td>
        <td>${m.read_at ? "read" : "unread"}</td>
      </tr>`).join("");
    return `<div class="panel" style="margin-bottom:12px"><h3>${esc(label)}</h3>${table(["Subject", "Type", "Dir", "Read"], body)}</div>`;
  }

  function renderInbox() {
    const inbox = overview.inbox || [];
    const by = (pred) => inbox.filter(pred);
    $("view-inbox").innerHTML = [
      inboxGroup("Buyer commercial (company facade)", by((m) => !["VEND-A", "VEND-C", "VEND-B"].includes(m.counterparty) && m.channel !== "EDI")),
      inboxGroup("Vendor A", by((m) => m.counterparty === "VEND-A")),
      inboxGroup("Vendor C", by((m) => m.counterparty === "VEND-C")),
      inboxGroup("Vendor B (fixture, no mailbox)", by((m) => m.counterparty === "VEND-B")),
      inboxGroup("Carrier / EDI", by((m) => m.channel === "EDI" || /speedy/i.test(m.counterparty || ""))),
    ].join("");
  }

  function renderHitl() {
    const rows = overview.waiting && overview.waiting.length
      ? overview.waiting
      : (overview.transactions || []).filter((t) => isHitl(t) || isException(t));
    $("view-hitl").innerHTML = `
      <div class="panel">
        <h3>Waiting on a human or exception on the same job</h3>
        ${rows.length ? table(["PR", "Kind", "Why", ""], rows.map((t) => `
          <tr class="clickable" data-open-tx="${t.transaction_id || ""}">
            <td class="mono">${esc(t.pr_id || t.transaction_id || t.request_id)}</td>
            <td>${pill(t.kind || t.stage)}</td>
            <td>${esc(t.question || t.halt_reason || (isException(t) ? "Delay or break — same job, not a separate incident." : "Approval required."))}</td>
            <td>
              ${isModelHold(t) ? modelButtons(t) : ""}
              ${isHitl(t) && t.stage === "PO_PENDING_APPROVAL" && !isModelHold(t) ? `<button class="primary" data-decide="${t.transaction_id}|approve_po">Approve PO</button>` : ""}
              ${isHitl(t) && t.stage !== "PO_PENDING_APPROVAL" && !isModelHold(t) ? `<button class="primary" data-decide="${t.transaction_id}|approve_exception">Approve exception</button>
              <button class="ghost" data-decide="${t.transaction_id}|cancel">Cancel</button>
              <button class="ghost" data-decide="${t.transaction_id}|keep_waiting">Keep waiting</button>` : ""}
              ${t.transaction_id ? `<button class="ghost" data-run="${t.transaction_id}">${isException(t) ? "Run recovery" : "Run"}</button>` : ""}
            </td>
          </tr>`).join("")) : `<div class="empty">Nothing needs you. Policy: ±5% price, 5-day slip, $5,000 approval. Model HITL appears when an agent is unsure.</div>`}
      </div>`;
  }

  function renderMemory() {
    const mem = overview.memory || {};
    const block = (title, why, rows, cols) => `
      <div class="panel"><h3>${title}</h3>
        <p class="body muted" style="padding-bottom:0">${why}</p>
        ${table(cols, (rows || []).map((r) => `<tr>${cols.map((c) => `<td>${esc(fmt(r[c]))}</td>`).join("")}</tr>`).join(""))}
      </div>`;
    $("view-memory").innerHTML = `<div class="grid-2">
      ${block("Supplier", "Did this vendor answer, and was the bid legal?", mem.supplier, ["vendor_id", "response_hours", "reliability_note"])}
      ${block("Lane", "How long did this plant–region actually take?", mem.lane, ["region", "carrier_name", "transit_days"])}
      ${block("Commercial", "Quoted vs paid after match.", mem.commercial, ["vendor_id", "material_id", "quoted_price", "paid_price"])}
      ${block("Exception", "What recovery worked on a delay or match break?", mem.exception, ["root_cause", "recovery_action", "effective"])}
    </div>`;
  }

  function renderPlaybooks() {
    $("view-playbooks").innerHTML = (overview.playbooks || []).map((s) => `
      <div class="panel" style="margin-bottom:12px">
        <h3>${esc(s.title)}</h3>
        <div class="body">
          <p>${esc(s.trigger)}</p>
          <p class="muted">${esc(s.agents || "")}</p>
          <ol>${(s.steps || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol>
          <div class="row">
            ${s.transaction_id ? `<button class="ghost" data-open-tx="${s.transaction_id}">Open job</button>` : ""}
            ${s.ask ? `<button class="ghost" data-ask="${esc(s.ask)}">Ask copilot</button>` : ""}
            ${s.transaction_id ? `<button class="primary" data-run="${s.transaction_id}">Run</button>` : ""}
          </div>
        </div>
      </div>`).join("");
  }

  function renderAll() {
    const k = overview.kpis || {};
    const policy = team.policy || {};
    $("policy-bar").textContent =
      `Policy: quotes must stay within ±${policy.price_deviation_pct || 5}% of last price. Delay slip ≤ ${policy.date_slip_days || 5} days auto-rebooks. PO over $${Number(policy.approval_amount_usd || 5000).toLocaleString()} needs a human.`;
    $("site-label").textContent = (overview.site && overview.site.plant) || "WH-FR-01";
    updateJobChip();
    renderTeam();
    renderJobs();
    renderInbox();
    renderHitl();
    renderMemory();
    renderPlaybooks();
    void k;
  }

  function showView(name) {
    Object.keys(views).forEach((v) => {
      const el = $("view-" + v);
      if (el) el.classList.toggle("hidden", v !== name);
    });
    document.querySelectorAll(".nav .item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    $("view-title").textContent = views[name][0];
    $("view-sub").textContent = views[name][1];
    $("shell-nav").classList.remove("open");
  }

  async function openTx(id, view) {
    if (!id) return;
    selectedId = id;
    const url = new URL(location.href);
    url.searchParams.set("tx", id);
    history.replaceState({}, "", url);
    const res = await fetch("/v1/execution/transactions/" + id);
    if (!res.ok) { alert("Could not load job"); return; }
    detail = await res.json();
    updateJobChip();
    renderAll();
    showView(view || "jobs");
  }

  let runPoll = null;
  function startRunPoll() {
    if (runPoll) return;
    runPoll = setInterval(() => { if (busy) load(); }, 3000);
  }
  function stopRunPoll() {
    if (!runPoll) return;
    clearInterval(runPoll);
    runPoll = null;
  }

  async function runTx(id) {
    if (!id || busy) return;
    busy = true;
    startRunPoll();
    renderJobs();
    try {
      const res = await fetch("/v1/execution/transactions/" + id + "/run", { method: "POST" });
      const payload = await res.json();
      if (!res.ok) { alert(payload.detail || "Run failed"); return; }
      await load();
      await openTx(id, "jobs");
    } finally {
      busy = false;
      stopRunPoll();
      renderJobs();
    }
  }

  async function decideTx(id, action, option) {
    if (!id || busy) return;
    busy = true;
    startRunPoll();
    try {
      const payloadBody = { action, wait: true };
      if (option) payloadBody.option = option;
      const res = await fetch("/v1/execution/transactions/" + id + "/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payloadBody),
      });
      const payload = await res.json();
      if (!res.ok) { alert(payload.detail || "Decision failed"); return; }
      await load();
      if (payload.transaction_id || selectedId) await openTx(payload.transaction_id || selectedId || id, "jobs");
    } finally {
      busy = false;
      stopRunPoll();
    }
  }

  function setCopilotOpen(open) {
    $("drawer").classList.toggle("open", open);
    $("overlay").classList.toggle("show", open);
    if (open) $("q").focus();
  }

  function addMsg(role, text, cards) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.appendChild(document.createTextNode(text));
    (cards || []).forEach((card) => {
      const c = document.createElement("div");
      c.className = "card";
      if (card.kind === "event") {
        c.innerHTML = "<strong>" + (card.title || "Proposed action") + "</strong><div>" + (card.summary || "") + "</div>";
        const yes = document.createElement("button");
        yes.className = "primary";
        yes.textContent = "Confirm";
        yes.onclick = () => send("yes", card.proposal_id, null);
        const no = document.createElement("button");
        no.className = "ghost";
        no.textContent = "Cancel";
        no.onclick = () => send("no", null, card.proposal_id);
        const row = document.createElement("div");
        row.style.marginTop = "8px";
        row.appendChild(yes);
        row.appendChild(no);
        c.appendChild(row);
      } else if (card.kind === "query") {
        c.innerHTML = "<strong>Read query</strong> · " + (card.row_count || 0) + " row(s)"
          + (card.error ? "<div>" + card.error + "</div>" : "")
          + "<pre>" + (card.sql || "") + "</pre>";
      }
      el.appendChild(c);
    });
    $("log").appendChild(el);
    el.scrollIntoView({ block: "end" });
  }

  async function send(message, confirmId, cancelId) {
    if (copilotBusy) return;
    copilotBusy = true;
    addMsg("user", message, []);
    try {
      const res = await fetch("/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          session_id: sessionId,
          user_role: $("role").value,
          confirm_proposal_id: confirmId,
          cancel_proposal_id: cancelId,
          transaction_id: selectedId,
          wait_for_workflow: false,
        }),
      });
      const payload = await res.json();
      sessionId = payload.session_id;
      addMsg("assistant", payload.reply || "(no reply)", payload.cards || []);
    } catch (err) {
      addMsg("assistant", "Copilot request failed: " + err, []);
    } finally {
      copilotBusy = false;
    }
  }

  function ask(text) {
    setCopilotOpen(true);
    send(text, null, null);
  }

  async function load() {
    const [oRes, tRes] = await Promise.all([
      fetch("/v1/execution/overview"),
      fetch("/v1/execution/team"),
    ]);
    overview = await oRes.json();
    team = tRes.ok ? await tRes.json() : { members: [], policy: {} };
    if (selectedId) {
      try {
        const dres = await fetch("/v1/execution/transactions/" + selectedId);
        if (dres.ok) detail = await dres.json();
      } catch (_) { /* keep last detail */ }
    }
    renderAll();
  }

  document.querySelectorAll(".nav .item").forEach((b) => {
    b.onclick = () => showView(b.dataset.view);
  });
  $("nav-toggle").onclick = () => $("shell-nav").classList.toggle("open");
  $("open-copilot").onclick = () => setCopilotOpen(true);
  $("fab").onclick = () => setCopilotOpen(true);
  $("close-copilot").onclick = () => setCopilotOpen(false);
  $("overlay").onclick = () => setCopilotOpen(false);
  document.body.addEventListener("click", (e) => {
    const agent = e.target.closest("[data-agent]");
    if (agent) {
      selectedAgent = agent.dataset.agent;
      renderTeam();
      return;
    }
    const openTxEl = e.target.closest("[data-open-tx]");
    if (openTxEl && openTxEl.dataset.openTx) { openTx(openTxEl.dataset.openTx); return; }
    const runBtn = e.target.closest("[data-run]");
    if (runBtn) { runTx(runBtn.dataset.run); return; }
    const decideBtn = e.target.closest("[data-decide]");
    if (decideBtn) {
      const parts = String(decideBtn.dataset.decide || "").split("|");
      decideTx(parts[0], parts[1] || "approve_exception", parts[2] || null);
      return;
    }
    const t = e.target.closest("[data-ask]");
    if (t && t.dataset.ask) ask(t.dataset.ask);
  });
  $("f").onsubmit = (e) => {
    e.preventDefault();
    const text = $("q").value.trim();
    if (!text) return;
    $("q").value = "";
    send(text, null, null);
  };

  load().then(() => {
    const params = new URLSearchParams(location.search);
    const tx = params.get("tx");
    if (tx) return openTx(tx);
  }).then(() => {
    if (new URLSearchParams(location.search).get("copilot") === "1") setCopilotOpen(true);
  });
  setInterval(load, 15000);
})();
