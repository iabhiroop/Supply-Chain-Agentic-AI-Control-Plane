export default function TwinPanel({ snap, links }) {
  const plants = snap?.plants_at_risk?.plants || [];
  const cover = snap?.days_of_cover?.positions || [];
  const pos = snap?.open_pos?.purchase_orders || [];
  const news = snap?.news?.signals || [];
  const tools = snap?.mcp?.tools || [];
  const down =
    snap?.plants_at_risk?.unavailable ||
    snap?.days_of_cover?.unavailable;

  return (
    <aside className="twin-panel panel">
      <header>
        <p className="kicker">Digital twin</p>
        <h3>World snapshot</h3>
      </header>
      {down ? (
        <p className="mute">Twin not reachable.</p>
      ) : (
        <>
          <h4>Plants at risk</h4>
          <ul>
            {plants.slice(0, 6).map((p) => (
              <li key={p.plant_id}>
                <code>{p.plant_id}</code> {p.days_of_cover}d cover
                {p.open_qty_from_abc != null ? ` · ${p.open_qty_from_abc} on ABC` : ""}
              </li>
            ))}
            {!plants.length ? <li className="mute">None.</li> : null}
          </ul>
          <h4>Days of cover</h4>
          <ul>
            {cover.slice(0, 6).map((p) => (
              <li key={`${p.plant_id}-${p.material_id || ""}`}>
                <code>{p.plant_id}</code> {p.days_of_cover}d
              </li>
            ))}
          </ul>
          <h4>Open POs (VEND-ABC)</h4>
          <ul>
            {pos.slice(0, 5).map((p) => (
              <li key={p.po_id}>
                <code>{p.po_id}</code> × {p.quantity}
              </li>
            ))}
            {!pos.length ? <li className="mute">None.</li> : null}
          </ul>
          <h4>News</h4>
          <ul>
            {news.slice(0, 3).map((n) => (
              <li key={n.signal_id || n.headline}>
                {n.headline || n.title || n.signal_id}
              </li>
            ))}
            {!news.length ? <li className="mute">No signals.</li> : null}
          </ul>
          {tools.length ? (
            <p className="mute">MCP: {tools.join(", ")}</p>
          ) : null}
        </>
      )}
      {links?.twin ? (
        <p>
          <a href={links.twin} target="_blank" rel="noreferrer">
            Open twin page
          </a>
        </p>
      ) : null}
    </aside>
  );
}
