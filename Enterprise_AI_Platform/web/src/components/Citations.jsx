export default function Citations({ citations }) {
  const retrieval = citations?.retrieval || citations || {};
  const paths = retrieval.graph_paths || citations?.graph_paths || citations?.paths || citations?.hits || [];
  const docs = retrieval.passages || citations?.documents || citations?.chunks || [];
  const template = citations?.graph_template || retrieval.graph_template;
  if (citations?.unavailable) {
    return <p className="mute">GraphRAG not reachable — citations skipped.</p>;
  }
  if (!citations) return null;
  return (
    <section className="panel">
      <h3>GraphRAG paths {template ? <code>{template}</code> : null}</h3>
      <ul className="paths">
        {(Array.isArray(paths) ? paths : []).slice(0, 8).map((p, i) => (
          <li key={i}>
            <code>{typeof p === "string" ? p : p.path || JSON.stringify(p)}</code>
          </li>
        ))}
        {!paths?.length && docs?.length
          ? docs.slice(0, 6).map((d, i) => (
              <li key={`d${i}`}>
                {d.title || d.doc_id || d.source_uri || JSON.stringify(d).slice(0, 160)}
              </li>
            ))
          : null}
        {!paths?.length && !docs?.length ? (
          <li className="mute">{citations.answer || "No path payload returned."}</li>
        ) : null}
      </ul>
    </section>
  );
}
