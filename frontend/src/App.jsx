import { useCallback, useEffect, useState } from "react";
import "./App.css";

const REFRESH_MS = 5000;

async function getJson(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }

  return data;
}

function StatusPill({ good, children }) {
  return (
    <span className={`pill ${good ? "pill-good" : "pill-warn"}`}>
      <span className="pill-dot" />
      {children}
    </span>
  );
}

function Metric({ label, value, detail, tone = "" }) {
  return (
    <article className="metric card">
      <span className="eyebrow">{label}</span>
      <strong className={`metric-value ${tone}`}>{value}</strong>
      <span className="muted metric-detail">{detail}</span>
    </article>
  );
}

function formatMoney(value, currency = "USDT") {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";

  return `${number.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 8,
  })} ${currency}`;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [portfolio, setPortfolio] = useState(null);
  const [errors, setErrors] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activePage, setActivePage] = useState("overview");
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");

  const refresh = useCallback(async (manual = false) => {
    if (manual) {
      setManualRefreshing(true);
      setRefreshMessage("");
    }

    const nextErrors = [];

    try {
      try {
        const data = await getJson("/health");
        setHealth(data);
      } catch {
        setHealth(null);
        nextErrors.push("API health endpoint is unreachable.");
      }

      try {
        const data = await getJson("/api/v1/status");
        setStatus(data);
      } catch {
        setStatus(null);
        nextErrors.push("Bot status could not be retrieved.");
      }

      try {
        const data = await getJson("/api/v1/portfolio");
        setPortfolio(data);
      } catch (error) {
        setPortfolio(null);
        nextErrors.push(error.message || "Portfolio is unavailable.");
      }

      setErrors(nextErrors);
      setLastUpdated(new Date());
      setLoading(false);

      if (manual) {
        setRefreshMessage(
          nextErrors.length === 0
            ? "Data refreshed successfully."
            : `Refresh completed with ${nextErrors.length} issue(s).`,
        );
      }
    } finally {
      if (manual) setManualRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const initialRefresh = window.setTimeout(refresh, 0);
    const timer = window.setInterval(refresh, REFRESH_MS);

    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const recoveryReady =
    status?.recovery_preflight_passed === true &&
    status?.recovery_checks_passed === true;

  const entriesLocked =
    status?.entries_authorized !== true &&
    status !== null;

  const killSwitch = status?.kill_switch_state ?? "UNKNOWN";
  const apiHealthy = health?.status === "ok";
  const positions = portfolio?.positions ?? [];

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">C</div>
          <div>
            <strong>CRYPTON</strong>
            <span>TRADING SYSTEM</span>
          </div>
        </div>

        <div className="side-label">WORKSPACE</div>
        <button
          type="button"
          className={`nav-item nav-button ${activePage === "overview" ? "nav-active" : ""}`}
          aria-current={activePage === "overview" ? "page" : undefined}
          onClick={() => setActivePage("overview")}
        >
          <span>?</span> Overview
        </button>
        <button
          type="button"
          className={`nav-item nav-button ${activePage === "portfolio" ? "nav-active" : ""}`}
          aria-current={activePage === "portfolio" ? "page" : undefined}
          onClick={() => setActivePage("portfolio")}
        >
          <span>?</span> Portfolio
          <small>Read-only</small>
        </button>
        <button
          type="button"
          className={`nav-item nav-button ${activePage === "orders" ? "nav-active" : ""}`}
          aria-current={activePage === "orders" ? "page" : undefined}
          onClick={() => setActivePage("orders")}
        >
          <span>?</span> Orders
          <small>Read-only</small>
        </button>

        <div className="sidebar-bottom">
          <div className="lock-icon">⌑</div>
          <strong>Execution locked</strong>
          <p>This dashboard cannot place or cancel orders.</p>
          <span className="sidebar-tag">MONITORING ONLY</span>
        </div>
      </aside>

      <section className="main-content">
        <div hidden={activePage !== "overview"}>
        <header className="topbar">
          <div>
            <div className="breadcrumb">Workspace / Overview</div>
            <h1>System overview</h1>
            <p className="muted">Monitor your bot and persisted state.</p>
          </div>
          <div className="top-actions">
            <StatusPill good={apiHealthy}>
              {apiHealthy ? "API connected" : "API unavailable"}
            </StatusPill>
            <button
              className="refresh-button"
              onClick={() => refresh(true)}
              disabled={manualRefreshing}
              aria-busy={manualRefreshing}
            >
              {manualRefreshing ? "Refreshing?" : "? Refresh"}
            </button>
          </div>
        </header>

        {refreshMessage && (
          <p className="refresh-feedback" role="status" aria-live="polite">
            {refreshMessage}
          </p>
        )}

        <div className="notice">
          <span className="notice-icon">ⓘ</span>
          <div>
            <strong>Read-only monitoring</strong>
            <p>
              Live trading is not enabled by this dashboard. Entry
              authorization remains disabled.
            </p>
          </div>
          <span className="notice-lock">LOCKED</span>
        </div>

        {errors.length > 0 && (
          <div className="error-banner" role="status">
            <strong>Some data is unavailable</strong>
            {errors.map((error) => <p key={error}>{error}</p>)}
            <span>Last checked: {lastUpdated?.toLocaleTimeString() ?? "—"}</span>
          </div>
        )}

        <div className="section-heading">
          <div>
            <h2>System health</h2>
            <p className="muted">Current process and recovery checks</p>
          </div>
          <span className="updated">
            {loading
              ? "Loading…"
              : `Updated ${lastUpdated?.toLocaleTimeString() ?? "—"}`}
          </span>
        </div>

        <div className="metrics-grid">
          <Metric
            label="TRADING MODE"
            value={status?.configured_mode ?? "UNKNOWN"}
            detail="Configured operating mode"
          />
          <Metric
            label="RECOVERY STATUS"
            value={recoveryReady ? "READY" : "NOT READY"}
            detail={status?.recovery_message ?? "Waiting for API"}
            tone={recoveryReady ? "tone-good" : "tone-warn"}
          />
          <Metric
            label="KILL SWITCH"
            value={killSwitch}
            detail="Persisted safety state"
            tone={killSwitch === "STOPPED" ? "tone-warn" : ""}
          />
          <Metric
            label="UNRESOLVED ORDERS"
            value={status?.unresolved_order_count ?? "—"}
            detail="Reported by recovery inspection"
            tone={
              status?.unresolved_order_count > 0 ? "tone-warn" : ""
            }
          />
        </div>

        <div className="content-grid">
          <section className="card portfolio-card">
            <div className="card-heading">
              <div>
                <h2>Portfolio snapshot</h2>
                <p className="muted">Persisted accounting data</p>
              </div>
              <StatusPill good={portfolio !== null}>
                {portfolio ? "Available" : "Unavailable"}
              </StatusPill>
            </div>

            {portfolio ? (
              <>
                <div className="balance-block">
                  <span className="eyebrow">AVAILABLE CASH</span>
                  <strong>
                    {formatMoney(portfolio.cash, portfolio.quote_currency)}
                  </strong>
                  <span className="muted">
                    Initial balance:{" "}
                    {formatMoney(
                      portfolio.initial_cash,
                      portfolio.quote_currency,
                    )}
                  </span>
                </div>
                <div className="portfolio-stats">
                  <div>
                    <span className="muted">Realized P&amp;L</span>
                    <strong>
                      {formatMoney(
                        portfolio.realized_pnl,
                        portfolio.quote_currency,
                      )}
                    </strong>
                  </div>
                  <div>
                    <span className="muted">Fees paid</span>
                    <strong>
                      {formatMoney(
                        portfolio.fees_paid,
                        portfolio.quote_currency,
                      )}
                    </strong>
                  </div>
                </div>
                <div className="valuation-note">
                  <span>ⓘ</span>
                  Market valuation is not calculated by this API.
                  {portfolio.valuation?.available === false &&
                    ` ${portfolio.valuation.reason}`}
                </div>
              </>
            ) : (
              <div className="empty-state">
                <div className="empty-icon">◫</div>
                <strong>Portfolio not available</strong>
                <p>
                  {status?.portfolio_error ||
                    "A valid persisted portfolio is required. No balance is being assumed."}
                </p>
                <span>HTTP 503 is expected until state is initialized.</span>
              </div>
            )}
          </section>

          <section className="card safety-card">
            <div className="card-heading">
              <div>
                <h2>Safety controls</h2>
                <p className="muted">Execution permission overview</p>
              </div>
              <span className="shield">⬡</span>
            </div>

            <div className="safety-row">
              <span>Live trading</span>
              <StatusPill good={status?.live_trading_enabled === false}>
                {status?.live_trading_enabled === false
                  ? "Disabled"
                  : status?.live_trading_enabled === true
                    ? "Enabled in config"
                    : "Unknown"}
              </StatusPill>
            </div>
            <div className="safety-row">
              <span>Entry authorization</span>
              <StatusPill good={entriesLocked}>
                {entriesLocked ? "Locked" : "Unknown"}
              </StatusPill>
            </div>
            <div className="safety-row">
              <span>Recovery preflight</span>
              <StatusPill good={status?.recovery_preflight_passed === true}>
                {status?.recovery_preflight_passed === true
                  ? "Passed"
                  : "Not passed"}
              </StatusPill>
            </div>
            <div className="safety-row">
              <span>Recovery checks</span>
              <StatusPill good={status?.recovery_checks_passed === true}>
                {status?.recovery_checks_passed === true
                  ? "Passed"
                  : "Not passed"}
              </StatusPill>
            </div>

            <div className="safety-footer">
              <span className="safety-dot" />
              No trading actions are available in this interface.
            </div>
          </section>
        </div>

        <section className="card positions-card">
          <div className="card-heading">
            <div>
              <h2>Open positions</h2>
              <p className="muted">Persisted positions only; no live valuation</p>
            </div>
            <span className="count-badge">{positions.length}</span>
          </div>

          {portfolio && positions.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Quantity</th>
                    <th>Cost basis</th>
                    <th>Average entry</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((position) => (
                    <tr key={position.asset}>
                      <td><strong>{position.asset}</strong></td>
                      <td>{position.quantity}</td>
                      <td>{position.cost_basis}</td>
                      <td>{position.average_entry_price}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="table-empty">
              {portfolio
                ? "No persisted open positions."
                : "Positions cannot be shown until portfolio data is available."}
            </div>
          )}
        </section>

        <footer className="page-footer">
          <span>CRYPTON <span className="footer-separator">/</span> Local monitoring</span>
          <span>Auto-refresh: 5 seconds · No order execution</span>
        </footer>
        </div>


        {activePage === "portfolio" && (
          <div className="page-view">
            <header className="topbar">
              <div>
                <div className="breadcrumb">Workspace / Portfolio</div>
                <h1>Portfolio</h1>
                <p className="muted">Read-only persisted accounting data.</p>
              </div>
              <StatusPill good={portfolio !== null}>
                {portfolio ? "Available" : "Unavailable"}
              </StatusPill>
            </header>

            {errors.length > 0 && (
              <div className="error-banner" role="status">
                <strong>Some data is unavailable</strong>
                {errors.map((error) => <p key={error}>{error}</p>)}
              </div>
            )}

            <section className="card page-card">
              <h2>Account summary</h2>
              {portfolio ? (
                <>
                  <div className="balance-block">
                    <span className="eyebrow">AVAILABLE CASH</span>
                    <strong>{formatMoney(portfolio.cash, portfolio.quote_currency)}</strong>
                    <span className="muted">
                      Initial balance: {formatMoney(portfolio.initial_cash, portfolio.quote_currency)}
                    </span>
                  </div>
                  <div className="portfolio-stats">
                    <div>
                      <span className="muted">Realized P&amp;L</span>
                      <strong>{formatMoney(portfolio.realized_pnl, portfolio.quote_currency)}</strong>
                    </div>
                    <div>
                      <span className="muted">Fees paid</span>
                      <strong>{formatMoney(portfolio.fees_paid, portfolio.quote_currency)}</strong>
                    </div>
                  </div>
                  <div className="valuation-note">
                    Market valuation is not calculated by this read-only API.
                  </div>
                </>
              ) : (
                <div className="empty-state">
                  <strong>Portfolio unavailable</strong>
                  <p>{status?.portfolio_error || "Persisted portfolio data is unavailable."}</p>
                </div>
              )}
            </section>

            <section className="card page-card">
              <div className="card-heading">
                <div>
                  <h2>Persisted positions</h2>
                  <p className="muted">No live market valuation</p>
                </div>
                <span className="count-badge">{positions.length}</span>
              </div>
              {portfolio && positions.length > 0 ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Asset</th>
                        <th>Quantity</th>
                        <th>Cost basis</th>
                        <th>Average entry</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((position) => (
                        <tr key={position.asset}>
                          <td><strong>{position.asset}</strong></td>
                          <td>{position.quantity}</td>
                          <td>{position.cost_basis}</td>
                          <td>{position.average_entry_price}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="table-empty">
                  {portfolio ? "No persisted open positions." : "Positions are unavailable."}
                </div>
              )}
            </section>
          </div>
        )}

        {activePage === "orders" && (
          <div className="page-view">
            <header className="topbar">
              <div>
                <div className="breadcrumb">Workspace / Orders</div>
                <h1>Orders</h1>
                <p className="muted">Read-only order recovery summary.</p>
              </div>
              <StatusPill good={status !== null}>
                {status ? "API connected" : "Unavailable"}
              </StatusPill>
            </header>

            {errors.length > 0 && (
              <div className="error-banner" role="status">
                <strong>Some data is unavailable</strong>
                {errors.map((error) => <p key={error}>{error}</p>)}
              </div>
            )}

            <section className="card page-card">
              <h2>Order recovery summary</h2>
              <div className="metrics-grid">
                <Metric
                  label="UNRESOLVED ORDERS"
                  value={status?.unresolved_order_count ?? "?"}
                  detail="Count reported by backend recovery checks."
                  tone={status?.unresolved_order_count > 0 ? "tone-warn" : ""}
                />
                <Metric
                  label="ENTRY AUTHORIZATION"
                  value={status?.entries_authorized === true ? "Authorized" : "Locked"}
                  detail="This dashboard does not authorize order entry."
                  tone="tone-warn"
                />
              </div>
              <div className="valuation-note">
                Complete order history is not exposed by the current API.
                This view shows only the unresolved-order count reported
                by the backend. It cannot place or cancel orders.
              </div>
            </section>
          </div>
        )}
      </section>
    </main>
  );
}
