import { money } from "../api";

export default function BudgetChips({ budget }) {
  if (!budget || budget.unavailable) {
    return <p className="chip-row mute">Gateway budgets unavailable.</p>;
  }
  const named = budget.named_agents || {};
  const entries = Object.entries(named);
  if (!entries.length) {
    return (
      <p className="chip-row mute">
        Project remaining {money(budget.remaining_usd, 2)}
      </p>
    );
  }
  return (
    <ul className="chip-row">
      {entries.map(([agent, row]) => (
        <li key={agent} className="pill">
          <code>{agent.replace(/_Agent$|_Orchestrator$/, "")}</code>{" "}
          {money(row.remaining_usd, 2)} / {money(row.limit_usd, 0)} · {row.rpm} rpm
        </li>
      ))}
    </ul>
  );
}
