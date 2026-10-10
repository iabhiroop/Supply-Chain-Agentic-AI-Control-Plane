import { dashboardHref } from "../api";

function isPoHold(tx) {
  return tx.stage === "PO_PENDING_APPROVAL" && tx.kind !== "model";
}

function isModelHold(tx) {
  return tx.kind === "model" || (tx.options && tx.options.length > 0 && tx.request_id);
}

function decideId(tx) {
  return tx.decide_id || tx.transaction_id || tx.request_id;
}

export default function HitlStrip({ inbox, busy, onDecide, links }) {
  const waiting = inbox?.waiting || [];
  const kpis = inbox?.kpis || {};
  return (
    <section className="hitl-strip">
      <header>
        <p className="kicker">Needs you</p>
        <span className="mute">
          {kpis.hitl ?? waiting.length} waiting · {kpis.exceptions ?? 0} exceptions
        </span>
      </header>
      {inbox?.unavailable ? (
        <p className="mute">Layer 2 inbox unavailable.</p>
      ) : waiting.length === 0 ? (
        <p className="mute">Nothing needs you. Policy: ±5% price, 5-day slip, $5,000 approval.</p>
      ) : (
        <ul>
          {waiting.map((tx) => (
            <li key={tx.request_id || tx.transaction_id || tx.pr_id}>
              <div>
                <code>{tx.pr_id || tx.transaction_id || tx.request_id}</code>{" "}
                <span className="pill">{tx.kind || tx.stage}</span>{" "}
                <span className="mute">{tx.question || tx.halt_reason || tx.status}</span>
              </div>
              <div className="row">
                {isModelHold(tx) ? (
                  (tx.options || []).map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      disabled={busy}
                      className={opt.id === tx.recommended_option ? "" : "ghost"}
                      onClick={() => onDecide(decideId(tx), "answer", opt.id)}
                    >
                      {opt.label || opt.id}
                    </button>
                  ))
                ) : isPoHold(tx) ? (
                  <button type="button" disabled={busy} onClick={() => onDecide(tx.transaction_id, "approve_po")}>
                    Approve PO
                  </button>
                ) : (
                  <>
                    <button type="button" disabled={busy} onClick={() => onDecide(tx.transaction_id, "approve_exception")}>
                      Approve exception
                    </button>
                    <button type="button" className="ghost" disabled={busy} onClick={() => onDecide(tx.transaction_id, "cancel")}>
                      Cancel
                    </button>
                    <button type="button" className="ghost" disabled={busy} onClick={() => onDecide(tx.transaction_id, "keep_waiting")}>
                      Keep waiting
                    </button>
                  </>
                )}
                <a className="ghost link-btn" href={dashboardHref(links, tx.transaction_id)} target="_blank" rel="noreferrer">
                  Open in Agent command
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
