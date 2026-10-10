import { linksOf } from "../api";

const KEYS = ["os", "gateway", "layer2", "twin", "graphrag", "eval"];

export default function StackDots({ stack }) {
  const links = linksOf(stack);
  return (
    <ul className="stack-dots">
      {KEYS.map((k) => {
        const row = stack?.[k];
        const ok = row?.ok;
        return (
          <li key={k} className={ok ? "up" : "down"}>
            <a href={links[k] || "#"} target="_blank" rel="noreferrer">
              <span className="dot" />
              {k}
            </a>
          </li>
        );
      })}
    </ul>
  );
}
