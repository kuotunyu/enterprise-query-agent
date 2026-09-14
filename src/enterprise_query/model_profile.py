"""Frozen proposal; selecting this profile does not authorize expenditure."""
MODEL_PROFILE = {
    'provider': 'openai', 'model': 'gpt-5.6-luna',
    'reasoning': {'effort': 'low'}, 'max_output_tokens': 4096,
    'service_tier': 'default', 'max_retries': 0,
    'price_checked_date': '2026-09-14',
    'price_source': 'https://developers.openai.com/api/docs/pricing',
    'usd_per_million_tokens': {
        'input': '0.20', 'cached_input': '0.02', 'cache_write': '0.25',
        'output': '1.20', 'long_input': '0.40',
        'long_cache_write': '0.50', 'long_output': '1.80',
    },
    'authorization': 'model selected; spending requires separate approval',
}
