import { pct, money } from "../api";

function Chip({ label, card }) {
  if (!card || card.available === false) {
    return (
      <span className="pill mute">
        {label}: —
      </span>
    );
  }
  const ok = card.success;
  return (
    <span className={`pill ${ok ? "good" : "bad"}`}>
      {label} {ok ? "PASS" : "FAIL"} · {pct((card.correctness || 0) * 100)} · {money(card.cost_usd, 4)}
    </span>
  );
}

export default function Scorechip({ card }) {
  if (!card || card.unavailable) {
    return <p className="chip-row mute">Eval scorecard unavailable.</p>;
  }
  return (
    <p className="chip-row">
      <Chip label="Taiwan" card={card.taiwan || (card.success != null ? card : null)} />
      <Chip label="P2P" card={card.p2p} />
    </p>
  );
}
