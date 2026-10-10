import { pct } from "../api";

export default function RecCard({ rec, applyResult, busy, onApply, onUndo }) {
  if (!rec || !rec.short_plants) {
    return <p className="mute">No recommendation yet.</p>;
  }
  const alt = rec.alternate || {};
  const realloc = rec.reallocate || {};
  const actionId = applyResult?.action_id;
  return (
    <article className="rec">
      <header>
        <p className="kicker">Typed recommendation</p>
        <h2>{rec.summary || "Disruption recommendation"}</h2>
      </header>
      <dl className="facts">
        <div>
          <dt>Short plants</dt>
          <dd>
            {(rec.short_plants || []).map((p) => (
              <div key={p.plant_id}>
                <code>{p.plant_id}</code> {p.days_of_cover}d cover
                {p.shortage_units != null ? ` · ${p.shortage_units} short` : ""}
              </div>
            ))}
          </dd>
        </div>
        <div>
          <dt>Affected POs</dt>
          <dd>
            {(rec.affected_po_ids || []).map((id) => (
              <code key={id}>{id}</code>
            ))}
          </dd>
        </div>
        <div>
          <dt>Reallocate</dt>
          <dd>
            <code>{realloc.po_id}</code> {realloc.qty} units ({pct(realloc.pct_of_po)} of PO)
          </dd>
        </div>
        <div>
          <dt>Alternate</dt>
          <dd>
            <code>{alt.vendor_id}</code> lead {alt.lead_days}d · premium {pct(alt.premium_pct)} ·
            cover {pct(alt.cover_pct)}
          </dd>
        </div>
        <div>
          <dt>Expedite RFQ</dt>
          <dd>{rec.expedite_rfq ? "Yes" : "No"}</dd>
        </div>
      </dl>
      <div className="row rec-actions">
        <button type="button" disabled={busy} onClick={onApply}>
          Apply reallocation
        </button>
        <button type="button" className="ghost" disabled={busy || !actionId} onClick={() => onUndo(actionId)}>
          Undo
        </button>
      </div>
      {applyResult ? (
        <p className={applyResult.error || applyResult.applied === false ? "mute" : "good"}>
          {applyResult.already_applied
            ? applyResult.detail || "Already applied."
            : applyResult.applied
              ? `Moved ${applyResult.moved_qty} off ${applyResult.po_id}.`
              : applyResult.detail || applyResult.decision || JSON.stringify(applyResult)}
        </p>
      ) : null}
    </article>
  );
}
