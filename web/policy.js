/* The decision policy, in one place, so the browser and the Python judge can be compared.
 *
 * This is a mirror of `verdict_desk/agents_v2.py` (rules v3), not a second implementation
 * that drifts: `tests/test_policy_parity.py` runs this file over a grid of inputs and
 * fails when the state it returns differs from what the Python judge returns for the
 * same numbers. If you change a threshold in one place, the test tells you to change
 * the other.
 *
 * Pure data in, pure data out. No DOM, no network.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VerdictPolicy = api;
})(typeof self !== "undefined" ? self : this, function () {
  const THRESHOLD = 1.5;          // entry band on the edge
  const VOL_PCT = 1.5;            // ATR as % of price that counts as high volatility
  const MARKET_WINDOW_MINUTES = 75; // price freshness window; stale blocks the decision
  const SYSTEM_MAX_RISK_PCT = 25; // the user may lower the risk, never raise it past this

  const STATES = [
    ["BLOCKED_STALE", "the newest price is older than the market window", "no position (fail-closed), and the refusal is recorded"],
    ["LONG", "edge at or above the entry band", "sized position with an ATR stop and target"],
    ["SHORT", "edge at or below minus the entry band", "sized position with an ATR stop and target"],
    ["REDUCE_EXPOSURE", "both sides at or above the band and volatility high", "no new position, existing exposure should shrink"],
    ["WATCH_REVERSAL", "one argument away from flipping the verdict", "no position, the deciding argument is named"],
    ["FLAT", "nothing close to the band", "no position"]
  ];

  function evaluate(input) {
    const bull = Number(input.bull) || 0;
    const bear = Number(input.bear) || 0;
    const atrPct = Number(input.atrPct) || 0;
    const ageMinutes = Number(input.ageMinutes) || 0;
    // A janela de frescor faz parte da regra vigente quando o registro foi gravado, e cada
    // registro carrega a sua. Re-derivar com a janela de hoje fara registros antigos parecerem
    // errados por um motivo que nao e erro deles.
    const windowMinutes = Number(input.windowMinutes) || MARKET_WINDOW_MINUTES;

    const edge = Math.round((bull - bear) * 10000) / 10000;
    const stale = ageMinutes > windowMinutes;
    if (stale) {
      return { state: "BLOCKED_STALE", edge, stale: true,
               rule: `price age ${ageMinutes} min > window ${windowMinutes} min` };
    }
    if (edge >= THRESHOLD) return { state: "LONG", edge, stale: false, rule: `bull ${bull} - bear ${bear} = ${edge} >= ${THRESHOLD}` };
    if (edge <= -THRESHOLD) return { state: "SHORT", edge, stale: false, rule: `bull ${bull} - bear ${bear} = ${edge} <= -${THRESHOLD}` };

    const min = Math.min(bull, bear);
    if (min >= THRESHOLD && atrPct >= VOL_PCT) {
      return { state: "REDUCE_EXPOSURE", edge, stale: false,
               rule: `both sides >= ${THRESHOLD} (min ${min}) and ATR ${atrPct}% >= ${VOL_PCT}%` };
    }
    if (edge - bull <= -THRESHOLD) {
      return { state: "WATCH_REVERSAL", edge, stale: false,
               rule: `edge ${edge} inside the band, but losing the bull argument (${bull}) flips it to SHORT` };
    }
    if (edge + bear >= THRESHOLD) {
      return { state: "WATCH_REVERSAL", edge, stale: false,
               rule: `edge ${edge} inside the band, but losing the bear argument (${bear}) flips it to LONG` };
    }
    return { state: "FLAT", edge, stale: false, rule: `edge ${edge} inside the band, nothing worth flipping it` };
  }

  function riskPct(requested) {
    if (requested == null) return null;
    const value = Number(requested);
    if (!(value > 0)) throw new Error(`risk pct must be greater than 0, got ${requested}`);
    if (value > SYSTEM_MAX_RISK_PCT) throw new Error(`risk pct ${value} exceeds the system cap of ${SYSTEM_MAX_RISK_PCT}%`);
    return value;
  }

  const STOP_ATR = 1.5;
  const TAKE_ATR = 3.0;
  const round = (x, places) => { const f = 10 ** places; return Math.round(x * f) / f; };

  /* The practice position a LONG or SHORT becomes, sized under the user's risk limit.
   * A mirror of the sizing in `agents_v2.judge`: the stop sits 1.5 ATR away, the target 3 ATR, and the
   * size is the cash the user is willing to lose (capital x risk%) divided by the stop distance. */
  function position(entry, atr, action, riskPercent, capital) {
    if (action !== "LONG" && action !== "SHORT") throw new Error(`no position for ${action}`);
    const pct = riskPct(riskPercent == null ? 1 : riskPercent);
    const cap = capital == null ? 10000 : Number(capital);
    const direction = action === "LONG" ? 1 : -1;
    const stopDistance = atr * STOP_ATR;
    const riskCash = cap * pct / 100;
    return {
      entry,
      stop: round(entry - direction * stopDistance, 6),
      take_profit: round(entry + direction * atr * TAKE_ATR, 6),
      size: round(riskCash / stopDistance, 8),
      risk_cash: round(riskCash, 6),
      risk_pct: pct,
      stop_distance: round(stopDistance, 6),
    };
  }

  return { THRESHOLD, VOL_PCT, MARKET_WINDOW_MINUTES, SYSTEM_MAX_RISK_PCT, STATES, evaluate, riskPct, position };
});
