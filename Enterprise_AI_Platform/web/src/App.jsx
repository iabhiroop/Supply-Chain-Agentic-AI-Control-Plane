import { useEffect, useMemo, useState } from "react";
import "./App.css";
import { PR1001, TAIWAN, api, dashboardHref, linksOf } from "./api";
import BudgetChips from "./components/BudgetChips.jsx";
import Citations from "./components/Citations.jsx";
import HitlStrip from "./components/HitlStrip.jsx";
import RecCard from "./components/RecCard.jsx";
import Scorechip from "./components/Scorechip.jsx";
import StackDots from "./components/StackDots.jsx";
import TraceDrawer from "./components/TraceDrawer.jsx";
import TwinPanel from "./components/TwinPanel.jsx";

export default function App() {
  const [tab, setTab] = useState("ask");
  const [question, setQuestion] = useState(TAIWAN);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [stack, setStack] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [p2p, setP2p] = useState(null);
  const [inbox, setInbox] = useState(null);
  const [budget, setBudget] = useState(null);
  const [scorecard, setScorecard] = useState(null);
  const [twin, setTwin] = useState(null);
  const [applyResult, setApplyResult] = useState(null);

  const links = useMemo(() => linksOf(stack), [stack]);

  async function refreshSlices() {
    const [health, box, bud, card, snap] = await Promise.all([
      api("/v1/health/stack").catch(() => null),
      api("/v1/inbox").catch(() => ({ unavailable: true })),
      api("/v1/budget").catch(() => ({ unavailable: true })),
      api("/v1/scorecard").catch(() => ({ unavailable: true })),
      api("/v1/twin/snapshot").catch(() => null),
    ]);
    setStack(health);
    setInbox(box);
    setBudget(bud);
    setScorecard(card);
    setTwin(snap);
  }

  useEffect(() => {
    refreshSlices();
    const t = setInterval(refreshSlices, 20000);
    return () => clearInterval(t);
  }, []);

  const agents = useMemo(() => result?.completed_agents || [], [result]);

  async function onAsk(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setApplyResult(null);
    try {
      const data = await api("/v1/ask", {
        method: "POST",
        body: JSON.stringify({ question, include_chat: true, include_graphrag: true, include_eval: true }),
      });
      setResult(data);
      setDrawer(true);
      if (data.scorecard) setScorecard(data.scorecard);
      if (data.gateway?.budget) setBudget(data.gateway.budget);
      refreshSlices();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function loadP2P() {
    try {
      const data = await api(`/v1/p2p/${PR1001}`);
      setP2p(data);
    } catch (err) {
      setP2p({ error: err.message || String(err) });
    }
  }

  useEffect(() => {
    if (tab === "p2p") loadP2P();
  }, [tab]);

  async function onP2P() {
    setBusy(true);
    setError("");
    try {
      const data = await api("/v1/p2p/run", {
        method: "POST",
        body: JSON.stringify({ transaction_id: PR1001 }),
      });
      void data;
      await loadP2P();
      refreshSlices();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onDecide(transactionId, action, option) {
    setBusy(true);
    setError("");
    try {
      await api(`/v1/hitl/${transactionId}/decide`, {
        method: "POST",
        body: JSON.stringify({ action, wait: true, option: option || null }),
      });
      await refreshSlices();
      if (tab === "p2p") await loadP2P();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onApply() {
    if (!result?.recommendation) return;
    setBusy(true);
    setError("");
    try {
      const data = await api("/v1/disruption/apply", {
        method: "POST",
        body: JSON.stringify({ recommendation: result.recommendation, decision: "approve" }),
      });
      setApplyResult(data);
      refreshSlices();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onUndo(actionId) {
    if (!actionId) return;
    setBusy(true);
    setError("");
    try {
      const data = await api("/v1/disruption/undo", {
        method: "POST",
        body: JSON.stringify({ action_id: actionId }),
      });
      setApplyResult(data);
      refreshSlices();
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  const tx = p2p?.transaction || p2p || {};

  return (
    <>
    <div className="background-blobs" aria-hidden="true">
      <div className="blob primary" />
      <div className="blob secondary" />
    </div>
    <div className="shell">
      <header className="mast">
        <div>
          <p className="kicker">Layer 5 · Operating Platform</p>
          <h1>Supply-chain sitting</h1>
        </div>
        <StackDots stack={stack} />
      </header>

      <div className="mast-meta">
        <BudgetChips budget={budget} />
        <Scorechip card={scorecard} />
      </div>

      <HitlStrip inbox={inbox} busy={busy} onDecide={onDecide} links={links} />

      <nav className="tabs">
        <button type="button" className={tab === "ask" ? "on" : ""} onClick={() => setTab("ask")}>
          Taiwan ask
        </button>
        <button type="button" className={tab === "p2p" ? "on" : ""} onClick={() => setTab("p2p")}>
          Run PR-1001
        </button>
      </nav>

      {error ? <p className="banner bad">{error}</p> : null}

      <div className="workspace">
        {tab === "ask" ? (
          <main>
            <form className="ask" onSubmit={onAsk}>
              <label htmlFor="q">Question</label>
              <textarea id="q" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
              <div className="row">
                <button type="submit" disabled={busy}>
                  {busy ? "Running layers…" : "Ask the stack"}
                </button>
                <button type="button" className="ghost" disabled={!result} onClick={() => setDrawer(true)}>
                  Trace drawer
                </button>
              </div>
            </form>

            {result?.narrative?.text ? <p className="narrative">{result.narrative.text}</p> : null}

            <RecCard
              rec={result?.recommendation}
              applyResult={applyResult}
              busy={busy}
              onApply={onApply}
              onUndo={onUndo}
            />

            {agents.length ? (
              <p className="agents">
                Specialists: {agents.map((a) => a.replace(/_Agent$/, "").replace(/_/g, " ")).join(" → ")}
              </p>
            ) : null}

            <Citations citations={result?.citations} />
          </main>
        ) : (
          <main>
            <p>
              Proxies Layer 2 <code>POST /v1/execution/transactions/…/run</code>. Job canvas stays on Agent command.
            </p>
            <p>
              <code>{tx.pr_id || "PR-1001"}</code>{" "}
              <span className="pill">{tx.stage || "—"}</span>{" "}
              <span className="pill">{tx.status || "—"}</span>
            </p>
            {tx.halt_reason ? <p className="banner">{tx.halt_reason}</p> : null}
            <div className="row">
              <button type="button" onClick={onP2P} disabled={busy}>
                {busy ? "Running PR-1001…" : "Run PR-1001"}
              </button>
              <a className="ghost link-btn" href={dashboardHref(links, PR1001)} target="_blank" rel="noreferrer">
                Open in Agent command
              </a>
            </div>
          </main>
        )}
        <TwinPanel snap={twin} links={links} />
      </div>

      <TraceDrawer open={drawer} onClose={() => setDrawer(false)} payload={result} />
    </div>
    </>
  );
}
