"""Adaptive learning for Verdict Desk agents.

Analyzes past dossier outcomes and adjusts argument weights to improve
decision quality over time. Learns from:
1. Which arguments led to profitable positions
2. Which arguments led to stopped-out positions  
3. Feature patterns that predict successful trades

Usage:
  python3 -m verdict_desk learn              # Show current adaptive weights
  python3 -m verdict_desk learn --apply      # Apply learned weights to v2
  python3 -m verdict_desk learn --reset      # Reset to default weights
"""

import json
import os
from pathlib import Path
from collections import Counter

LEARNING_FILE = Path("data/learned_weights.json")


def load_learned_weights(ledger_path: Path = Path("ledger/ledger.jsonl")) -> dict:
    """Analyze ledger and compute adaptive weights based on outcomes."""
    if not ledger_path.exists():
        return {}
    
    dossiers = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    if len(dossiers) < 10:
        return {}
    
    # Import portfolio to get position outcomes
    from verdict_desk import portfolio
    summary = portfolio.summarize(dossiers)
    positions = summary['positions']
    
    # Map dossier seq to position outcome
    seq_to_pnl = {}
    for p in positions:
        seq_to_pnl[p['seq']] = {
            'pnl': p['pnl'],
            'status': p['status'],
            'action': p['action'],
            'bars': p.get('bars', 0)
        }
    
    # Analyze which arguments led to good vs bad outcomes
    arg_performance = {}  # claim -> {wins, losses, total, avg_pnl}
    feature_success = {}  # feature_key -> {success_rate, avg_edge}
    
    for d in dossiers:
        seq = d['seq']
        outcome = seq_to_pnl.get(seq)
        pnl = outcome['pnl'] if outcome else 0
        is_win = pnl > 0 if outcome else None
        
        # Analyze arguments
        for side in ['bull', 'bear']:
            for arg in d.get(side, []):
                claim = arg['claim']
                weight = arg['weight']
                if claim not in arg_performance:
                    arg_performance[claim] = {'wins': 0, 'losses': 0, 'total': 0, 'pnl_sum': 0, 'weight': weight}
                arg_performance[claim]['total'] += 1
                arg_performance[claim]['weight'] = weight
                if is_win is not None:
                    if is_win:
                        arg_performance[claim]['wins'] += 1
                    else:
                        arg_performance[claim]['losses'] += 1
                    arg_performance[claim]['pnl_sum'] += pnl
        
        # Analyze features
        feats = d.get('features', {})
        action = d['verdict']['action']
        edge = d['verdict']['edge']
        
        if outcome is not None:
            for feat_key in ['return_24', 'volume_ratio', 'rsi_14', 'macd_histogram', 'bb_pct_b']:
                val = feats.get(feat_key)
                if val is not None:
                    if feat_key not in feature_success:
                        feature_success[feat_key] = {'values': [], 'pnls': [], 'actions': []}
                    feature_success[feat_key]['values'].append(val)
                    feature_success[feat_key]['pnls'].append(pnl)
                    feature_success[feat_key]['actions'].append(action)
    
    # Compute adaptive weights
    adaptive = {}
    for claim, stats in arg_performance.items():
        if stats['total'] >= 2:
            win_rate = stats['wins'] / max(stats['wins'] + stats['losses'], 1)
            avg_pnl = stats['pnl_sum'] / max(stats['wins'] + stats['losses'], 1)
            # Adjust weight: higher for winning arguments, lower for losing
            adjustment = 1.0 + (win_rate - 0.5) * 0.4  # +/- 20% adjustment
            adaptive[claim] = {
                'original_weight': stats['weight'],
                'adaptive_weight': round(stats['weight'] * adjustment, 2),
                'win_rate': round(win_rate, 2),
                'avg_pnl': round(avg_pnl, 2),
                'sample_size': stats['wins'] + stats['losses']
            }
    
    # Feature insights
    feature_insights = {}
    for feat_key, data in feature_success.items():
        if len(data['values']) >= 5:
            avg_val = sum(data['values']) / len(data['values'])
            pnl_when_high = sum(p for v, p in zip(data['values'], data['pnls']) if v > avg_val) / max(sum(1 for v in data['values'] if v > avg_val), 1)
            pnl_when_low = sum(p for v, p in zip(data['values'], data['pnls']) if v <= avg_val) / max(sum(1 for v in data['values'] if v <= avg_val), 1)
            feature_insights[feat_key] = {
                'avg_value': round(avg_val, 4),
                'pnl_when_high': round(pnl_when_high, 2),
                'pnl_when_low': round(pnl_when_low, 2),
                'edge_signal': 'bullish' if pnl_when_high > pnl_when_low else 'bearish'
            }
    
    return {
        'dossiers_analyzed': len(dossiers),
        'positions_analyzed': len([p for p in positions if p['status'] != 'OPEN']),
        'adaptive_weights': adaptive,
        'feature_insights': feature_insights,
        'generated_at': __import__('datetime').datetime.now().isoformat()
    }


def print_learning_report(ledger_path: Path = Path("ledger/ledger.jsonl")):
    """Print a human-readable learning report."""
    learned = load_learned_weights(ledger_path)
    
    if not learned:
        print("Not enough data for learning (need 10+ dossiers with closed positions)")
        return
    
    print("=" * 60)
    print("🧠 VERDICT DESK — LEARNING REPORT")
    print("=" * 60)
    print()
    print(f"Dossiers analyzed: {learned['dossiers_analyzed']}")
    print(f"Positions analyzed: {learned['positions_analyzed']}")
    print()
    
    if learned['adaptive_weights']:
        print("=== ADAPTIVE WEIGHTS ===")
        for claim, data in sorted(learned['adaptive_weights'].items(), 
                                   key=lambda x: x[1]['adaptive_weight'], reverse=True):
            orig = data['original_weight']
            adapt = data['adaptive_weight']
            win_rate = data['win_rate']
            pnl = data['avg_pnl']
            samples = data['sample_size']
            direction = "↑" if adapt > orig else "↓" if adapt < orig else "="
            print(f"  {claim}")
            print(f"    weight: {orig} → {adapt} ({direction}) | win: {win_rate:.0%} | avg pnl: {pnl:.1f} | n={samples}")
        print()
    
    if learned['feature_insights']:
        print("=== FEATURE INSIGHTS ===")
        for feat, data in learned['feature_insights'].items():
            signal = data['edge_signal']
            print(f"  {feat}: avg={data['avg_value']:.2f} | high={data['pnl_when_high']:.1f} | low={data['pnl_when_low']:.1f} | signal: {signal}")
        print()


def apply_learned_weights(rules_version: str = "v2"):
    """Apply learned weights to the agent configuration.
    
    This writes optimized weights to a config file that agents_v2 can load.
    """
    learned = load_learned_weights()
    if not learned or not learned['adaptive_weights']:
        print("No adaptive weights to apply (not enough data)")
        return
    
    # Save learned config
    LEARNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEARNING_FILE.write_text(json.dumps(learned, indent=2))
    print(f"Learned weights saved to {LEARNING_FILE}")
    print(f"  {len(learned['adaptive_weights'])} arguments analyzed")
    print(f"  {len(learned['feature_insights'])} feature insights")
    
    return learned


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply learned weights")
    parser.add_argument("--reset", action="store_true", help="Reset learned weights")
    args = parser.parse_args()
    
    if args.reset:
        if LEARNING_FILE.exists():
            LEARNING_FILE.unlink()
            print(f"Removed {LEARNING_FILE}")
    elif args.apply:
        apply_learned_weights()
    else:
        print_learning_report()
