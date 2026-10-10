import { money, pct } from "../api";

export default function TraceDrawer({ open, onClose, payload }) {
  if (!open) return null;
  const usage = payload?.usage || {};
  const scores = payload?.eval?.scores || [];
  const hard = scores.find((s) => s.metric === "hard_blockers");
  const tools = payload?.tools || {};
  const graph = payload?.graphrag || {};
  const gateway = payload?.gateway || {};
  const named = gateway?.budget?.named_agents || {};
  const card = payload?.scorecard || {};
  const cardScores = card.success != null ? card : card.taiwan || {};
  return (
    <aside className="drawer">
      <header>
        <h3>Trace</h3>
        <button type="button" className="text" onClick={onClose}>
          Close
        </button>
      </header>
      <p>
        <span className="kicker">trace_id</span>
        <code className="block">{payload?.trace_id || "—"}</code>
      </p>
      <section>
        <h4>MCP tools</h4>
        <p className="mute">{tools.note || "Twin MCP is the documented tool door."}</p>
        <ul className="scores">
          {(tools.used || []).map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h4>GraphRAG template</h4>
        <p>
          <code>{graph.template || graph.planned_query?.template || "—"}</code>
        </p>
        {graph.planned_query?.key ? (
          <p className="mute">
            key <code>{graph.planned_query.key}</code>
          </p>
        ) : null}
      </section>
      <section>
        <h4>Gateway cache / budget</h4>
        <p>
          cache <code>{gateway.cache_status || (usage.cache_hit_count ? "hit" : "—")}</code>
          {gateway.route_hint ? (
            <>
              {" "}
              · hint <code>{gateway.route_hint}</code>
            </>
          ) : null}
        </p>
        <ul className="scores">
          {Object.entries(named).map(([agent, row]) => (
            <li key={agent}>
              <code>{agent}</code> ${Number(row.remaining_usd ?? 0).toFixed(2)} / ${Number(row.limit_usd ?? 0).toFixed(0)} · {row.rpm} rpm
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h4>Eval scorecard</h4>
        {cardScores.success != null ? (
          <p className={cardScores.success ? "good" : "bad"}>
            {cardScores.success ? "PASS" : "FAIL"} · correctness {pct((cardScores.correctness || 0) * 100)} · tokens{" "}
            {cardScores.tokens ?? "—"} · {money(cardScores.cost_usd)}
          </p>
        ) : hard ? (
          <p className={hard.passed ? "good" : "bad"}>
            {hard.passed ? "PASS" : "FAIL"} — {hard.rationale}
          </p>
        ) : (
          <p className="mute">No eval scores (Layer 4 down or not scored).</p>
        )}
        <ul className="scores">
          {scores
            .filter((s) => s.metric !== "hard_blockers")
            .map((s) => (
              <li key={s.metric} className={s.passed ? "good" : "bad"}>
                {s.metric}: {s.passed ? "ok" : "fail"} — {s.rationale}
              </li>
            ))}
        </ul>
      </section>
      <section>
        <h4>Gateway usage</h4>
        {usage.unavailable ? (
          <p className="mute">No usage for this trace (arithmetic path uses no LLM, or Gateway down).</p>
        ) : (
          <>
            <p>
              {usage.total_requests || 0} calls · {usage.total_tokens || 0} tokens · {money(usage.total_cost_usd)} ·
              cache {usage.cache_hit_count || 0}
            </p>
            <ul>
              {(usage.requests || []).map((r) => (
                <li key={r.gateway_request_id}>
                  <code>{r.agent_id || "—"}</code> {r.model} {r.cache_hit ? "HIT" : "MISS"} {money(r.cost_usd)}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </aside>
  );
}
