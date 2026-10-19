"""Audited historical backfill: link canonical diesel story to an MT5 M15 observation."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from story_intermarket_validation_engine import evaluate_story

CANONICAL = 'story_4eea0c019e02a0b0'
RELATED = 'story_68317846dddd9807'
TRIGGER = 'fast_7dc1052a23f19abf74ac'

def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)

def validate(stories, interpretations, reaction, window='post_60m'):
    matches = [s for s in stories['stories'] if s['story_id'] == CANONICAL]
    related = [s for s in stories['stories'] if s['story_id'] == RELATED]
    interpretations = [s for s in interpretations['interpretations'] if s['story_id'] == CANONICAL]
    if len(matches) != 1 or len(related) != 1 or len(interpretations) != 1:
        raise ValueError('Canonical, related and interpretation must each resolve exactly once')
    members = [m for m in matches[0]['members'] if m['event_id'] == TRIGGER]
    if len(members) != 1:
        raise ValueError('Missing or ambiguous trigger event')
    trigger = members[0]
    if reaction.get('event_id') != RELATED:
        raise ValueError('Reaction report does not match related story ID')
    trigger_time = utc(trigger['published_at_utc'])
    anchor_time = utc(reaction['event_time_utc'])
    delta = (trigger_time - anchor_time).total_seconds()
    if not (0 <= delta < 900) or anchor_time.second:
        raise ValueError('Reaction anchor must precede trigger by <15 minutes')
    related_time = utc(related[0]['first_seen_utc'])
    if related_time < trigger_time or (related_time - trigger_time).total_seconds() > 900:
        raise ValueError('Related detail must follow trigger within 15 minutes')
    # Original validator requires strict identity; preserve its contract by validating
    # a copy with canonical ID, and preserve original report ID in the bridge provenance.
    aligned_report = dict(reaction, event_id=CANONICAL)
    result = evaluate_story(interpretations[0], aligned_report, window=window)
    result['provenance'] = {
        'canonical_story_id': CANONICAL, 'trigger_event_id': TRIGGER,
        'trigger_time_utc': trigger['published_at_utc'],
        'reaction_report_original_event_id': reaction['event_id'],
        'related_story_id': RELATED, 'related_story_first_seen_utc': related[0]['first_seen_utc'],
        'relationship': 'related_supply_detail_manual_review',
        'anchor_offset_seconds': delta,
        'observed_at_utc': trigger.get('observed_at_utc'),
        'validation_mode': 'historical_backfill_not_realtime',
        'event_verification': trigger.get('verification', 'unverified_report'),
        'causal_attribution': False,
    }
    result['limitations'] = [
        'No yields or inflation expectations measurement',
        'USD breadth is a synthetic proxy, not DXY',
        'Other news within the reaction window; cannot isolate trigger',
        '24h wall-clock window crosses Friday market closure',
        'Source observed after publication; not a real-time latency test',
        'Manual cross-story relationship; not established by clustering engine',
    ]
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stories', type=Path, required=True)
    p.add_argument('--interpretations', type=Path, required=True)
    p.add_argument('--reaction', type=Path, required=True)
    p.add_argument('--output', type=Path, default=Path('data/news/diesel_story_validation.json'))
    p.add_argument('--window', choices=('post_15m','post_60m','post_4h','post_24h'), default='post_60m')
    args = p.parse_args()
    files = [json.loads(path.read_text(encoding='utf-8')) for path in (args.stories, args.interpretations, args.reaction)]
    result = validate(*files, window=args.window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Saved', args.output, [(m['mechanism_id'], m['status']) for m in result['mechanisms']])

if __name__ == '__main__':
    main()
