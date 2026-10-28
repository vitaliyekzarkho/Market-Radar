"""Translate existing measured reaction inputs; never invent time authority."""
from copy import deepcopy
from causal_mechanism_engine import propose_mechanisms
from story_intermarket_validation_engine import evaluate_story
from news_pa_context_adapter import explain_scenario

def connect_news(technical, reaction_payload, driver_payload, *, cutoff, max_age_seconds=3600):
    event=deepcopy(reaction_payload.get('event') or {})
    report=deepcopy(reaction_payload.get('reaction_report') or {})
    driver=deepcopy(driver_payload.get('driver_context') or {})
    identity=event.get('event_id')
    mechanism=propose_mechanisms(event) if event else {'candidates':[]}
    interpretation={'story_id':identity,'event_time_utc':event.get('event_time_utc') or event.get('date'),
                    'mechanism_candidates':deepcopy(mechanism.get('candidates') or [])}
    validation=None;reason='NO_MATCHING_EVENT_OR_REACTION'
    if identity and report.get('event_id')==identity:
        validation=evaluate_story(interpretation,report,'post_60m')
        reason='MATCHED_EVENT_DIRECTIONAL_CHECK_ONLY'
    # These values must originate upstream. Never substitute generated_at or cutoff.
    ia=event.get('available_at_utc');va=reaction_payload.get('available_at_utc')
    result=explain_scenario(technical,{'payload':interpretation,'available_at_utc':ia} if identity else None,
        {'payload':validation,'available_at_utc':va} if validation else None,
        as_of_utc=cutoff,max_age_seconds=max_age_seconds,legacy_role=driver.get('reaction_status'))
    states=[x['status'] for x in (validation or {}).get('mechanisms',[])]
    directional='insufficient_data'
    if states and set(states)=={'supported'}:directional='directionally_supported'
    elif states and set(states)=={'contradicted'}:directional='directionally_contradicted'
    return {'event_id':identity,'event':event,'interpretation':interpretation,'validation':validation,
            'expected':interpretation['mechanism_candidates'],'actual_reaction':report,
            'directional_comparison':directional,'adapter':result['news_context'],'reason':reason,
            'legacy_driver':driver,'causality_confirmed':False,'trading_permission':False,
            'times':{'market_event_at_utc':interpretation['event_time_utc'],'interpretation_available_at_utc':ia,
                     'reaction_available_at_utc':va,'evaluated_at_utc':cutoff,
                     'historical_authority':'UNKNOWN','local_receipt_does_not_prove_history':True}}
